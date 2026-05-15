import SwiftUI

/// iOS Settings-like 3-level menu. Agent navigates the path L1 → L2 → L3,
/// then toggles a specific switch from ON to OFF. Passing requires the
/// targeted toggle to flip from ON to OFF; touching any other toggle is
/// a fail.
///
/// Realism notes:
///   - Each level uses InsetGroupedListStyle for iOS-natural look.
///   - L1 has ~8 categories, L2 has ~6 items, L3 has ~6 toggles.
///   - Toggle labels at L3 are deliberately near-duplicates (e.g.
///     "Allow Apps to Request to Track" vs "Allow Tracking from Apps")
///     so the agent has to read carefully, not just match keywords.
///   - All toggles at L3 default to ON, so the agent must explicitly
///     turn one OFF.
///
/// Launch args:
///   -TargetPath <string>   "/"-separated path. Example:
///                          "Privacy/Tracking/Allow Apps to Request to Track"
///                          Default: "Privacy/Tracking/Allow Apps to Request to Track"
///   -Seed <int>            Reserved (layout order is deterministic for now)
///   -TimeoutS <double>     default 90
struct MenuToggleOffView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?
    @State private var toggleStates: [String: Bool] = [:]
    @State private var togglesTouched: [String] = []

    private var targetPath: String {
        config.string("TargetPath", default: "Privacy/Tracking/Allow Apps to Request to Track")
    }
    private var timeoutS: Double { config.double("TimeoutS", default: 90) }
    private var seed: Int { config.int("Seed", default: 1) }

    private var pathParts: [String] {
        targetPath.split(separator: "/").map(String.init)
    }

    private var params: [String: String] {
        [
            "target_path": targetPath,
            "seed": "\(seed)",
        ]
    }

    var body: some View {
        NavigationStack {
            Level1View(
                catalog: catalog,
                toggleStates: $toggleStates,
                onToggle: handleToggle
            )
            .navigationTitle("Settings")
        }
        .onAppear {
            // Seed initial toggle states: every L3 toggle defaults to ON.
            if toggleStates.isEmpty {
                for cat in catalog {
                    for sub in cat.subitems {
                        for tog in sub.toggles {
                            toggleStates[toggleKey(cat: cat.name, sub: sub.name, tog: tog)] = true
                        }
                    }
                }
            }
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("menu_toggle_off appeared target=\(targetPath)")
        }
    }

    private func handleToggle(category: String, subitem: String, toggle: String, newValue: Bool) {
        guard !completed else { return }
        let key = toggleKey(cat: category, sub: subitem, tog: toggle)
        toggleStates[key] = newValue
        togglesTouched.append(key)

        let pp = pathParts
        guard pp.count == 3 else { return }
        let targetKey = toggleKey(cat: pp[0], sub: pp[1], tog: pp[2])
        let elapsed = Date().timeIntervalSince(startedAt)

        if key == targetKey && newValue == false {
            // Right toggle flipped off. Check no other toggle was flipped.
            let extraFlipped = togglesTouched.filter { $0 != targetKey }
            if extraFlipped.isEmpty {
                completed = true
                timeoutTask?.cancel()
                Signal.pass(scenario: "menu_toggle_off", params: params, elapsed: elapsed)
            } else {
                completed = true
                timeoutTask?.cancel()
                Signal.fail(
                    scenario: "menu_toggle_off", params: params,
                    reason: "extra_toggle_flipped", elapsed: elapsed,
                    extras: ["touched": extraFlipped.joined(separator: ","),
                             "touched_count": "\(extraFlipped.count)"]
                )
            }
        } else if key == targetKey && newValue == true {
            // Agent flipped it back on after turning off; let them keep trying.
        } else {
            // Wrong toggle touched — immediate fail.
            completed = true
            timeoutTask?.cancel()
            Signal.fail(
                scenario: "menu_toggle_off", params: params,
                reason: "wrong_toggle", elapsed: elapsed,
                extras: ["touched": key, "expected": targetKey]
            )
        }
    }

    private func toggleKey(cat: String, sub: String, tog: String) -> String {
        "\(cat)/\(sub)/\(tog)"
    }

    private func scheduleTimeout() {
        timeoutTask = Task { [timeoutS] in
            try? await Task.sleep(nanoseconds: UInt64(timeoutS * 1_000_000_000))
            if Task.isCancelled || completed { return }
            await MainActor.run {
                guard !completed else { return }
                completed = true
                let elapsed = Date().timeIntervalSince(startedAt)
                Signal.fail(
                    scenario: "menu_toggle_off", params: params,
                    reason: "timeout", elapsed: elapsed,
                    extras: ["touched": togglesTouched.joined(separator: ",")]
                )
            }
        }
    }

    // MARK: - Static menu catalog (realistic-looking Settings hierarchy)

    private struct Category { let name: String; let icon: String; let subitems: [Subitem] }
    private struct Subitem { let name: String; let toggles: [String] }

    // Typed destination markers so each level's navigationDestination has its
    // own Hashable identity — otherwise two `navigationDestination(for: String.self)`
    // modifiers on the same NavigationStack collide and L3 routes nowhere.
    private struct L2Dest: Hashable { let category: String }
    private struct L3Dest: Hashable { let category: String; let subitem: String }

    private var catalog: [Category] {
        [
            Category(name: "General", icon: "gear", subitems: [
                Subitem(name: "Software Update", toggles: ["Automatic Updates", "Download iOS Updates", "Install Security Responses"]),
                Subitem(name: "AirDrop", toggles: ["Receiving Off", "Contacts Only", "Everyone for 10 Minutes"]),
            ]),
            Category(name: "Display & Brightness", icon: "sun.max", subitems: [
                Subitem(name: "Appearance", toggles: ["Automatic", "Dim with Dark Appearance"]),
                Subitem(name: "Auto-Lock", toggles: ["Raise to Wake", "Auto-Lock 30 Seconds"]),
            ]),
            Category(name: "Notifications", icon: "bell", subitems: [
                Subitem(name: "Notification Style", toggles: ["Show Previews", "Show in Notification Center"]),
                Subitem(name: "Scheduled Summary", toggles: ["Enable Scheduled Summary", "Show Next Summary"]),
            ]),
            Category(name: "Privacy", icon: "hand.raised", subitems: [
                Subitem(name: "Location Services", toggles: ["Location Services", "Share My Location", "System Services"]),
                Subitem(name: "Tracking", toggles: [
                    "Allow Apps to Request to Track",
                    "Allow Tracking from Apps",
                    "Show Tracking Requests",
                ]),
                Subitem(name: "Analytics & Improvements", toggles: ["Share iPhone Analytics", "Share iCloud Analytics", "Improve Siri & Dictation"]),
            ]),
            Category(name: "Screen Time", icon: "hourglass", subitems: [
                Subitem(name: "Downtime", toggles: ["Schedule", "Block at Downtime", "Allow Calls during Downtime"]),
                Subitem(name: "App Limits", toggles: ["Limit Social Apps", "Limit Games"]),
            ]),
            Category(name: "Accessibility", icon: "figure.wave", subitems: [
                Subitem(name: "Display & Text Size", toggles: ["Bold Text", "Larger Text", "Reduce Transparency"]),
                Subitem(name: "Motion", toggles: ["Reduce Motion", "Auto-Play Message Effects"]),
            ]),
            Category(name: "Battery", icon: "battery.100", subitems: [
                Subitem(name: "Battery Health", toggles: ["Optimized Battery Charging", "Clean Energy Charging"]),
                Subitem(name: "Low Power Mode", toggles: ["Low Power Mode", "Auto-Enable at 20%"]),
            ]),
            Category(name: "Sounds & Haptics", icon: "speaker.wave.2", subitems: [
                Subitem(name: "Ringer and Alerts", toggles: ["Change with Buttons", "Haptics in Silent Mode"]),
                Subitem(name: "Keyboard Feedback", toggles: ["Sound", "Haptic"]),
            ]),
        ]
    }

    // MARK: - Level views

    private struct Level1View: View {
        let catalog: [Category]
        @Binding var toggleStates: [String: Bool]
        let onToggle: (String, String, String, Bool) -> Void

        var body: some View {
            List {
                ForEach(catalog, id: \.name) { cat in
                    NavigationLink(value: L2Dest(category: cat.name)) {
                        HStack(spacing: 12) {
                            Image(systemName: cat.icon)
                                .frame(width: 28, height: 28)
                                .foregroundStyle(.secondary)
                            Text(cat.name).font(.body)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            // All destinations registered at the L1 root so each Hashable type
            // resolves unambiguously regardless of which level pushed it.
            .navigationDestination(for: L2Dest.self) { dest in
                if let cat = catalog.first(where: { $0.name == dest.category }) {
                    Level2View(category: cat, toggleStates: $toggleStates, onToggle: onToggle)
                        .navigationTitle(cat.name)
                }
            }
            .navigationDestination(for: L3Dest.self) { dest in
                if let cat = catalog.first(where: { $0.name == dest.category }),
                   let sub = cat.subitems.first(where: { $0.name == dest.subitem }) {
                    Level3View(
                        category: cat.name,
                        subitem: sub,
                        toggleStates: $toggleStates,
                        onToggle: onToggle
                    )
                    .navigationTitle(sub.name)
                }
            }
        }
    }

    private struct Level2View: View {
        let category: Category
        @Binding var toggleStates: [String: Bool]
        let onToggle: (String, String, String, Bool) -> Void

        var body: some View {
            List {
                ForEach(category.subitems, id: \.name) { sub in
                    NavigationLink(value: L3Dest(category: category.name, subitem: sub.name)) {
                        Text(sub.name).font(.body)
                    }
                }
            }
            .listStyle(.insetGrouped)
        }
    }

    private struct Level3View: View {
        let category: String
        let subitem: Subitem
        @Binding var toggleStates: [String: Bool]
        let onToggle: (String, String, String, Bool) -> Void

        var body: some View {
            List {
                ForEach(subitem.toggles, id: \.self) { tog in
                    let key = "\(category)/\(subitem.name)/\(tog)"
                    Toggle(isOn: Binding(
                        get: { toggleStates[key] ?? true },
                        set: { newValue in onToggle(category, subitem.name, tog, newValue) }
                    )) {
                        Text(tog).font(.body)
                    }
                }
            }
            .listStyle(.insetGrouped)
        }
    }
}
