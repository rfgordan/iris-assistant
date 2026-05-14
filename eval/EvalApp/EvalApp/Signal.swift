import Foundation
import os

/// Emits structured RESULT lines to the macOS unified log, where the harness
/// picks them up via `xcrun simctl spawn booted log stream`. The subsystem
/// "com.eval" is what the harness filters on.
enum Signal {
    private static let log = Logger(subsystem: "com.eval", category: "result")

    static func pass(scenario: String, params: [String: String], elapsed: TimeInterval) {
        emit(verdict: "pass", scenario: scenario, params: params, extras: ["elapsed_s": fmt(elapsed)])
    }

    static func fail(scenario: String, params: [String: String], reason: String, elapsed: TimeInterval, extras: [String: String] = [:]) {
        var all = extras
        all["reason"] = reason
        all["elapsed_s"] = fmt(elapsed)
        emit(verdict: "fail", scenario: scenario, params: params, extras: all)
    }

    private static func emit(verdict: String, scenario: String, params: [String: String], extras: [String: String]) {
        var parts = ["RESULT", verdict, "scenario=\(scenario)"]
        for k in params.keys.sorted() { parts.append("\(k)=\(params[k]!)") }
        for k in extras.keys.sorted() { parts.append("\(k)=\(extras[k]!)") }
        let line = parts.joined(separator: " ")
        log.notice("\(line, privacy: .public)")
    }

    private static func fmt(_ t: TimeInterval) -> String {
        String(format: "%.2f", t)
    }

    static func debug(_ msg: String) {
        log.notice("DEBUG \(msg, privacy: .public)")
    }
}
