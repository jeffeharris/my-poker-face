package com.mypokerface.app

import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import android.util.Log
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
import org.json.JSONObject

private const val TAG = "OnDeviceLLM"

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
                val status = model.checkStatus()
                Log.i(TAG, "availability: checkStatus=$status (AVAILABLE=${FeatureStatus.AVAILABLE})")
                when (status) {
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
                val status = model.checkStatus()
                Log.i(TAG, "prewarm: checkStatus=$status")
                when (status) {
                    FeatureStatus.AVAILABLE -> {
                        model.warmup()
                        Log.i(TAG, "prewarm: model warmed")
                        call.resolve(JSObject().put("warmed", true))
                    }
                    FeatureStatus.DOWNLOADABLE -> {
                        // Fire-and-forget the download; report not-yet-warm.
                        Log.i(TAG, "prewarm: starting Gemini Nano model download")
                        scope.launch {
                            runCatching {
                                model.download().collect { st -> Log.i(TAG, "download: $st") }
                            }.onFailure { Log.w(TAG, "download failed: ${it.message}") }
                        }
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
                Log.i(TAG, "suggestChat: generating on-device (prompt ${full.length} chars)")
                // genai-prompt exposes a String overload of generateContent — no need
                // to build a GenerateContentRequest/TextPart for a plain text prompt.
                val response = model.generateContent(full)
                val text = response.candidates.firstOrNull()?.text
                    ?: throw IllegalStateException("empty response")
                val suggestions = parseSuggestions(text, tones.firstOrNull() ?: "")
                if (suggestions.length() == 0) throw IllegalStateException("no parseable suggestions")
                Log.i(TAG, "suggestChat: ON-DEVICE OK — ${suggestions.length()} suggestion(s)")
                call.resolve(JSObject().put("suggestions", suggestions))
            } catch (e: Throwable) {
                Log.w(TAG, "suggestChat: on-device failed (-> server): ${e.message}")
                call.reject("on-device generation failed: ${e.message}")
            }
        }
    }

    /**
     * Streaming variant of [suggestChat] — the Android side of the JS
     * `suggestChatStream` contract (callback return type: resolve() fires repeatedly
     * with cumulative `{suggestions, done}` until done=true). Collects the Gemini
     * Nano token stream via `generateContentStream` and surfaces each suggestion as
     * its JSON object closes, so the UI shows the first line before the rest finish.
     * The JS self-heals to non-streaming generation if this errors.
     */
    @PluginMethod(returnType = PluginMethod.RETURN_CALLBACK)
    fun suggestChatStream(call: PluginCall) {
        val prompt = call.getString("prompt")
        if (prompt.isNullOrEmpty()) {
            call.reject("prompt is required")
            return
        }
        val system = call.getString("system")
        val tones: List<String> = call.getArray("tones", JSArray())?.let { arr ->
            (0 until arr.length()).mapNotNull { arr.optString(it, null) }
        } ?: emptyList()
        val fallbackTone = tones.firstOrNull() ?: ""

        call.setKeepAlive(true)
        scope.launch {
            try {
                val full = buildPrompt(prompt, system, tones)
                Log.i(TAG, "suggestChatStream: streaming on-device (prompt ${full.length} chars)")
                var acc = ""
                var lastCount = 0
                var chunks = 0
                model.generateContentStream(full).collect { resp ->
                    chunks++
                    val text = resp.candidates.firstOrNull()?.text ?: ""
                    // Tolerate either cumulative or delta chunk semantics.
                    acc = if (text.startsWith(acc) && text.length >= acc.length) text else acc + text
                    val partial = parseStreamingSuggestions(acc, fallbackTone)
                    Log.i(TAG, "stream chunk #$chunks: +${text.length}c acc=${acc.length}c parsed=${partial.length()}")
                    if (partial.length() > lastCount) {
                        lastCount = partial.length()
                        call.resolve(JSObject().put("suggestions", partial).put("done", false))
                    }
                }
                val finalList = parseStreamingSuggestions(acc, fallbackTone)
                Log.i(TAG, "suggestChatStream: ON-DEVICE OK — ${finalList.length()} suggestion(s) in $chunks chunk(s)")
                Log.i(TAG, "stream raw: ${acc.replace("\n", "\\n").take(700)}")
                call.resolve(JSObject().put("suggestions", finalList).put("done", true))
            } catch (e: Throwable) {
                Log.w(TAG, "suggestChatStream: on-device failed (-> server): ${e.message}")
                call.reject("on-device streaming failed: ${e.message}")
            } finally {
                bridge?.releaseCall(call)
            }
        }
    }

    /**
     * Extract every COMPLETE top-level {…} object from (possibly still-unclosed) JSON
     * text and map to {text, tone}. Unlike parseSuggestions (which needs the whole
     * array), this lets a streamed, not-yet-closed array surface the suggestions whose
     * objects have already closed — enabling one-at-a-time reveal.
     */
    private fun parseStreamingSuggestions(raw: String, fallbackTone: String): JSArray {
        val out = JSArray()
        var depth = 0
        var start = -1
        var inStr = false
        var esc = false
        for (i in raw.indices) {
            val c = raw[i]
            if (inStr) {
                when {
                    esc -> esc = false
                    c == '\\' -> esc = true
                    c == '"' -> inStr = false
                }
                continue
            }
            when (c) {
                '"' -> inStr = true
                '{' -> { if (depth == 0) start = i; depth++ }
                '}' -> {
                    if (depth > 0) depth--
                    if (depth == 0 && start >= 0) {
                        try {
                            val o = JSONObject(raw.substring(start, i + 1))
                            val text = o.optString("text", "").trim()
                            if (text.isNotEmpty()) {
                                val tone = o.optString("tone", fallbackTone).trim().ifEmpty { fallbackTone }
                                out.put(JSObject().put("text", text).put("tone", tone))
                            }
                        } catch (_: Throwable) {
                        }
                        start = -1
                    }
                }
            }
        }
        return out
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
