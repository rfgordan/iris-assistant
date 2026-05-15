import Foundation
import SwiftUI

/// Reads launch arguments (via UserDefaults — Apple parses `-Key value` pairs
/// from the command line into the standard defaults) and exposes typed
/// accessors. Each scenario view reads what it needs.
struct ScenarioConfig {
    let scenario: String
    let raw: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.raw = defaults
        self.scenario = defaults.string(forKey: "Scenario") ?? "tap_target"
    }

    func int(_ key: String, default def: Int) -> Int {
        raw.object(forKey: key) == nil ? def : raw.integer(forKey: key)
    }

    func string(_ key: String, default def: String) -> String {
        raw.string(forKey: key) ?? def
    }

    func double(_ key: String, default def: Double) -> Double {
        raw.object(forKey: key) == nil ? def : raw.double(forKey: key)
    }
}

struct ScenarioRouter: View {
    let config: ScenarioConfig

    var body: some View {
        switch config.scenario {
        case "tap_target":
            TapTargetView(config: config)
        case "tap_with_distractors":
            TapWithDistractorsView(config: config)
        case "scroll_then_tap":
            ScrollThenTapView(config: config)
        case "multi_step_nav":
            MultiStepNavView(config: config)
        case "tap_in_order":
            TapInOrderView(config: config)
        case "find_odd_one":
            FindOddOneView(config: config)
        case "count_then_tap":
            CountThenTapView(config: config)
        case "menu_toggle_off":
            MenuToggleOffView(config: config)
        case "spotlight_search":
            SpotlightSearchView(config: config)
        case "scroll_find_message":
            ScrollFindMessageView(config: config)
        case "swipe_pages_find":
            SwipePagesFindView(config: config)
        default:
            UnknownScenarioView(name: config.scenario)
        }
    }
}

struct UnknownScenarioView: View {
    let name: String
    var body: some View {
        VStack(spacing: 12) {
            Text("Unknown scenario").font(.title)
            Text(name).font(.body.monospaced())
        }
        .onAppear {
            Signal.fail(scenario: name, params: [:], reason: "unknown_scenario", elapsed: 0)
        }
    }
}
