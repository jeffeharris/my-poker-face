package com.mypokerface.app

import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.google.mlkit.genai.common.FeatureStatus
import com.google.mlkit.genai.prompt.Generation
import com.google.mlkit.genai.prompt.GenerativeModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import org.json.JSONArray

/**
 * Android counterpart to iOS's FoundationModelsBridgePlugin.swift — on-device
 * chat-suggestion generation, here via **Gemini Nano** through ML Kit's GenAI
 * **Prompt API** (`com.google.mlkit:genai-prompt`). Apple uses Foundation Models;
 * Android uses Gemini Nano; the JS bridge (`src/utils/onDeviceLLM.ts`) and the
 * server-composes-parity routing in `api.ts` are identical and can't tell the
 * difference — which is why this registers under the SAME jsName, `FoundationModels`.
 * (The class is named for what it actually is; only the bridge id is shared.)
 *
 * PROOF OF CONCEPT, native-only and best-effort: when Gemini Nano is unavailable
 * (no AICore / unsupported device / not downloaded), `availability()` reports false
 * and callers fall back to the server route — same contract as iOS.
 *
 * Registered in MainActivity.onCreate (app-local plugins aren't auto-registered).
 */
@CapacitorPlugin(name = "FoundationModels")
class OnDeviceLLMPlugin : Plugin() {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    /** ML Kit GenAI client for the on-device prompt model (Gemini Nano via AICore). */
    private val model: GenerativeModel by lazy { Generation.getClient() }

    /**
     * Reports whether on-device generation can run right now. The JS bridge caches
     * this and only routes on-device when `available` is true. Only `AVAILABLE`
     * (model present + ready) counts; `DOWNLOADABLE`/`DOWNLOADING` report false (the
     * model isn't usable yet) — `prewarm()` kicks off the download so a later session
     * flips to available.
     */
    @PluginMethod
    fun availability(call: PluginCall) {
        scope.launch {
            try {
                when (model.checkStatus()) {
                    FeatureStatus.AVAILABLE ->
                        call.resolve(JSObject().put("available", true))
                    FeatureStatus.DOWNLOADABLE ->
                        call.resolve(JSObject().put("available", false).put("reason", "model downloadable (not yet downloaded)"))
                    FeatureStatus.DOWNLOADING ->
                        call.resolve(JSObject().put("available", false).put("reason", "model downloading"))
                    else ->
                        call.resolve(JSObject().put("available", false).put("reason", "Gemini Nano unavailable on this device"))
                }
            } catch (e: Throwable) {
                call.resolve(JSObject().put("available", false).put("reason", "genai unavailable: ${e.message}"))
            }
        }
    }

    /**
     * Pull the model into memory ahead of a request. If it's only DOWNLOADABLE,
     * start the (one-time, sizable) download so a future session can use it. Always
     * best-effort; generation still works without it, just colder.
     */
    @PluginMethod
    fun prewarm(call: PluginCall) {
        scope.launch {
            try {
                when (model.checkStatus()) {
                    FeatureStatus.AVAILABLE -> {
                        model.warmup()
                        call.resolve(JSObject().put("warmed", true))
                    }
                    FeatureStatus.DOWNLOADABLE -> {
                        // Fire-and-forget the download; report not-yet-warm.
                        scope.launch { runCatching { model.download().collect { } } }
                        call.resolve(JSObject().put("warmed", false))
                    }
                    else -> call.resolve(JSObject().put("warmed", false))
                }
            } catch (e: Throwable) {
                call.resolve(JSObject().put("warmed", false))
            }
        }
    }

    /**
     * Generate 2–4 quick-chat suggestions on-device. Mirrors the Swift `suggestChat`:
     * `prompt` is the client/server-composed context, optional `system` is the
     * server's exact instructions (server-composes parity), optional `tones` biases
     * tone. Rejects on any failure so the JS side falls back to the server route.
     *
     * Nano emits free text (no `@Generable` guided generation), so we instruct it to
     * return a JSON array of {text, tone} and parse that, tolerating fences/prose.
     */
    @PluginMethod
    fun suggestChat(call: PluginCall) {
        val prompt = call.getString("prompt")
        if (prompt.isNullOrEmpty()) {
            call.reject("prompt is required")
            return
        }
        val system = call.getString("system")
        val tones: List<String> = call.getArray("tones", JSArray())?.let { arr ->
            (0 until arr.length()).mapNotNull { arr.optString(it, null) }
        } ?: emptyList()

        scope.launch {
            try {
                val full = buildPrompt(prompt, system, tones)
                // genai-prompt exposes a String overload of generateContent — no need
                // to build a GenerateContentRequest/TextPart for a plain text prompt.
                val response = model.generateContent(full)
                val text = response.candidates.firstOrNull()?.text
                    ?: throw IllegalStateException("empty response")
                val suggestions = parseSuggestions(text, tones.firstOrNull() ?: "")
                if (suggestions.length() == 0) throw IllegalStateException("no parseable suggestions")
                call.resolve(JSObject().put("suggestions", suggestions))
            } catch (e: Throwable) {
                call.reject("on-device generation failed: ${e.message}")
            }
        }
    }

    private fun buildPrompt(prompt: String, system: String?, tones: List<String>): String {
        val instructions = system ?: """
            You write sharp, witty poker banter that reacts to the actual hand. Never
            generic — always a specific callback to what just happened. Short and punchy.
        """.trimIndent()
        val sb = StringBuilder(instructions).append("\n\n").append(prompt)
        if (system == null && tones.isNotEmpty()) {
            sb.append("\n\nFavor these tones: ").append(tones.joinToString(", ")).append(".")
        }
        sb.append(
            "\n\nReturn ONLY a compact JSON array of 2-4 objects, each " +
                "{\"text\": <chat line, under 15 words>, \"tone\": <one-word tone>}. " +
                "No markdown, no commentary."
        )
        return sb.toString()
    }

    /** Extract the first JSON array from the model's text and map it to [{text,tone}]. */
    private fun parseSuggestions(raw: String, fallbackTone: String): JSArray {
        val out = JSArray()
        val start = raw.indexOf('[')
        val end = raw.lastIndexOf(']')
        if (start < 0 || end <= start) return out
        val arr = try {
            JSONArray(raw.substring(start, end + 1))
        } catch (e: Throwable) {
            return out
        }
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            val text = obj.optString("text", "").trim()
            if (text.isEmpty()) continue
            val tone = obj.optString("tone", fallbackTone).trim().ifEmpty { fallbackTone }
            out.put(JSObject().put("text", text).put("tone", tone))
        }
        return out
    }
}
