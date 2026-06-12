import Foundation
import Capacitor

#if canImport(FoundationModels)
import FoundationModels
#endif

/// PROOF OF CONCEPT — on-device chat-suggestion generation via Apple's Foundation
/// Models framework (WWDC25, iOS 26+). Generates the quick-chat suggestions the
/// player picks from, fully on-device: no API cost, no network round-trip, offline,
/// private. The JS side (`src/utils/onDeviceLLM.ts`) tries this first and falls back
/// to the server route whenever the model is unavailable or errors.
///
/// Registration: app-local Capacitor plugins are NOT auto-registered (only npm
/// packages are). It is registered explicitly in `MainViewController.capacitorDidLoad()`
/// in `WidgetBridgePlugin.swift`, next to the WidgetBridge registration.
///
/// JS side: `registerPlugin('FoundationModels')` →
///   `FoundationModels.availability()` → `{ available, reason? }`
///   `FoundationModels.suggestChat({ prompt, tones })` → `{ suggestions: [{ text, tone }] }`
///
/// BUILD NOTE: this target only compiles in Xcode on macOS 26+ with the iOS 26 SDK —
/// it cannot be built from the Linux CI container. After editing the web bundle:
///   VITE_API_URL=https://mypokerfacegame.com VITE_SOCKET_URL=https://mypokerfacegame.com \
///     npm run build && npx cap sync ios && npx cap open ios
/// then build/run on an Apple-Intelligence-capable device or simulator.
@objc(FoundationModelsBridgePlugin)
public class FoundationModelsBridgePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FoundationModelsBridgePlugin"
    public let jsName = "FoundationModels"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "availability", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "suggestChat", returnType: CAPPluginReturnPromise)
    ]

    /// Reports whether the on-device model can be used right now. The JS bridge
    /// caches this and only routes to on-device generation when `available` is true.
    @objc func availability(_ call: CAPPluginCall) {
        #if canImport(FoundationModels)
        if #available(iOS 26.0, *) {
            switch SystemLanguageModel.default.availability {
            case .available:
                call.resolve(["available": true])
            case .unavailable(let reason):
                call.resolve(["available": false, "reason": "\(reason)"])
            }
            return
        }
        #endif
        call.resolve(["available": false, "reason": "FoundationModels unavailable (needs iOS 26+)"])
    }

    /// Generates 2–4 quick-chat suggestions for the current spot. `prompt` is the
    /// compact, client-built context; `tones` (optional) biases the requested tones.
    @objc func suggestChat(_ call: CAPPluginCall) {
        guard let prompt = call.getString("prompt"), !prompt.isEmpty else {
            call.reject("prompt is required")
            return
        }
        let tones = call.getArray("tones", String.self) ?? []
        // Optional server-composed instructions (server-composes parity mode). When
        // present, the server has already built the full prompt with real game
        // context, so we use its system text verbatim instead of our generic one.
        let system = call.getString("system")

        #if canImport(FoundationModels)
        if #available(iOS 26.0, *) {
            Task {
                do {
                    let suggestions = try await Self.generate(prompt: prompt, system: system, tones: tones)
                    call.resolve(["suggestions": suggestions.map { ["text": $0.text, "tone": $0.tone] }])
                } catch {
                    // Let the JS side fall back to the server route.
                    call.reject("on-device generation failed: \(error.localizedDescription)")
                }
            }
            return
        }
        #endif
        call.reject("FoundationModels unavailable (needs iOS 26+)")
    }

    #if canImport(FoundationModels)
    /// The structure the model is constrained to emit (guided generation).
    @available(iOS 26.0, *)
    @Generable
    struct ChatSuggestions {
        @Guide(description: "A short list of sharp poker chat lines, each under 15 words")
        var suggestions: [ChatSuggestion]
    }

    @available(iOS 26.0, *)
    @Generable
    struct ChatSuggestion {
        @Guide(description: "The chat line to send. Specific, witty, under 15 words.")
        var text: String
        @Guide(description: "One-word tone label, e.g. gloat, needle, props, salty, bravado")
        var tone: String
    }

    @available(iOS 26.0, *)
    private static func generate(
        prompt: String,
        system: String?,
        tones: [String]
    ) async throws -> [(text: String, tone: String)] {
        // Server-composes mode: use the server's system text as-is and don't append
        // our tone hint (the server prompt is already complete). Standalone mode
        // (e.g. the /dev/fmtest page): fall back to our generic instructions + hint.
        let instructions = system ?? """
        You write sharp, witty poker banter that reacts to the actual hand. \
        Never generic — always specific callbacks to what just happened. Short and punchy.
        """
        var fullPrompt = prompt
        if system == nil, !tones.isEmpty {
            fullPrompt += "\n\nFavor these tones: \(tones.joined(separator: ", "))."
        }

        let session = LanguageModelSession(instructions: instructions)
        let response = try await session.respond(to: fullPrompt, generating: ChatSuggestions.self)
        return response.content.suggestions.map { (text: $0.text, tone: $0.tone) }
    }
    #endif
}
