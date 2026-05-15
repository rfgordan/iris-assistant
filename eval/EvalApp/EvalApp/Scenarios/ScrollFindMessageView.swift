import SwiftUI

/// Messages-like conversation list. Agent must:
///   1. Scroll the list until the target row is visible
///   2. Tap that row → push a compose screen
///   3. Type the required message text
///   4. Tap Send
///
/// Realism notes:
///   - Each row: 28pt avatar circle + 17pt SemiBold name + 15pt gray snippet
///     + tiny timestamp. Row height ~74pt. Matches iOS Messages density.
///   - When Confusable=true the list includes near-duplicates of the
///     target name (e.g. "Sara Chen", "Sarah Cohen") to test reading
///     precision, not just keyword spotting.
///   - The target is placed mid-list (~row index Count/2) so the agent has
///     to scroll a real distance — not just at top or bottom.
///
/// Launch args:
///   -Count <int>             rows in the list (default 30)
///   -TargetName <string>     name of the target conversation (default "Sarah Chen")
///   -RequiredMessage <string> exact text the agent must type (default "On my way")
///   -Confusable <bool>       include near-duplicates (default true)
///   -Seed <int>              order of non-target rows (default 1)
///   -TimeoutS <double>       default 120
struct ScrollFindMessageView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?
    @State private var selectedRowIndex: Int? = nil
    @State private var composeText: String = ""
    @State private var path: [Int] = []

    private var count: Int { max(5, config.int("Count", default: 30)) }
    private var targetName: String { config.string("TargetName", default: "Sarah Chen") }
    private var requiredMessage: String { config.string("RequiredMessage", default: "On my way") }
    private var confusable: Bool {
        // UserDefaults bools come back as ints; treat "0"/"false" as false.
        let v = config.string("Confusable", default: "true").lowercased()
        return !(v == "false" || v == "0" || v == "no")
    }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 120) }

    private var params: [String: String] {
        [
            "count": "\(count)",
            "target_name": targetName,
            "required_message": requiredMessage,
            "confusable": "\(confusable)",
            "seed": "\(seed)",
        ]
    }

    // Stable per-launch ordering of rows. Target sits at ~middle of list.
    private var rows: [(name: String, snippet: String, time: String)] {
        var rng = SeededRNG(seed: UInt64(seed))
        let pool = filler(confusableAround: targetName, includeConfusables: confusable)
        var picks: [(String, String, String)] = []
        for _ in 0..<count {
            let name = pool[Int.random(in: 0..<pool.count, using: &rng)]
            let snippet = snippets[Int.random(in: 0..<snippets.count, using: &rng)]
            let time = times[Int.random(in: 0..<times.count, using: &rng)]
            picks.append((name, snippet, time))
        }
        // Place target at middle index so scroll distance is non-trivial.
        let targetIdx = count / 2
        let targetSnippet = snippets[Int.random(in: 0..<snippets.count, using: &rng)]
        let targetTime = times[Int.random(in: 0..<times.count, using: &rng)]
        picks[targetIdx] = (targetName, targetSnippet, targetTime)
        return picks
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                ForEach(Array(rows.enumerated()), id: \.offset) { idx, row in
                    Button {
                        handleRowTap(index: idx, name: row.name)
                    } label: {
                        HStack(alignment: .top, spacing: 12) {
                            Circle()
                                .fill(avatarColor(for: row.name))
                                .frame(width: 44, height: 44)
                                .overlay(
                                    Text(initials(for: row.name))
                                        .font(.system(size: 16, weight: .semibold))
                                        .foregroundStyle(.white)
                                )
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(row.name)
                                        .font(.system(size: 17, weight: .semibold))
                                        .foregroundStyle(.primary)
                                    Spacer()
                                    Text(row.time)
                                        .font(.system(size: 13))
                                        .foregroundStyle(.secondary)
                                }
                                Text(row.snippet)
                                    .font(.system(size: 15))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .buttonStyle(.plain)
                }
            }
            .listStyle(.plain)
            .navigationTitle("Messages")
            .navigationDestination(for: Int.self) { idx in
                composeScreen(for: rows[idx].name, idx: idx)
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("scroll_find_message appeared target=\(targetName)")
        }
    }

    @ViewBuilder
    private func composeScreen(for name: String, idx: Int) -> some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Text("To: \(name)")
                    .font(.system(size: 17, weight: .semibold))
                Spacer()
            }
            .padding()
            .background(Color(white: 0.96))

            Spacer()

            HStack(spacing: 8) {
                TextField("Message", text: $composeText, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                Button {
                    handleSend(toName: name)
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 32))
                        .foregroundStyle(.blue)
                }
                .disabled(composeText.isEmpty)
            }
            .padding(12)
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    private func handleRowTap(index: Int, name: String) {
        guard !completed else { return }
        selectedRowIndex = index
        composeText = ""
        path = [index]
        Signal.debug("row tapped idx=\(index) name=\(name)")
    }

    private func handleSend(toName: String) {
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        let nameMatches = toName == targetName
        let textMatches = composeText.trimmingCharacters(in: .whitespacesAndNewlines) == requiredMessage

        if nameMatches && textMatches {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "scroll_find_message", params: params, elapsed: elapsed)
        } else {
            completed = true
            timeoutTask?.cancel()
            let reason: String
            if !nameMatches { reason = "wrong_recipient" }
            else if composeText.isEmpty { reason = "empty_message" }
            else { reason = "wrong_message_text" }
            Signal.fail(
                scenario: "scroll_find_message", params: params,
                reason: reason, elapsed: elapsed,
                extras: ["sent_to": toName, "typed_text": composeText]
            )
        }
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
                    scenario: "scroll_find_message", params: params,
                    reason: "timeout", elapsed: elapsed,
                    extras: [
                        "selected_idx": selectedRowIndex.map(String.init) ?? "none",
                        "typed_text": composeText,
                    ]
                )
            }
        }
    }

    // MARK: - Filler content

    private func filler(confusableAround target: String, includeConfusables: Bool) -> [String] {
        var pool = [
            "Alex Rivera", "Brian Park", "Casey Wong", "Diana Lopez", "Ethan Brooks",
            "Fiona O'Hara", "Greg Walsh", "Hannah Kim", "Isaac Patel", "Julia Reyes",
            "Kevin Chen", "Lara Singh", "Mia Cohen", "Noah Adams", "Olivia Tran",
            "Priya Shah", "Quinn Liu", "Ryan Morales", "Sofia Russo", "Tom Hwang",
            "Uma Patel", "Victor Chu", "Wendy Park", "Xavier Diaz", "Yara Khan",
            "Zoe Mendel", "Aaron Goldberg", "Bella Marino", "Caleb Brown", "Devon Cole",
        ]
        if includeConfusables {
            // Add near-duplicates of the target name to force careful reading.
            let parts = target.split(separator: " ")
            if parts.count == 2 {
                let first = String(parts[0])
                let last = String(parts[1])
                // Trim/swap a letter for visual confusables.
                if first.count > 2 {
                    let dropped = String(first.dropLast())
                    pool.append("\(dropped) \(last)")
                }
                pool.append("\(first) \(last)son")
                if last.count > 2 {
                    let lastDropped = String(last.dropLast()) + "n"
                    pool.append("\(first) \(lastDropped)")
                }
            }
        }
        return pool
    }

    private let snippets = [
        "see you at 7", "did you grab the keys?", "running 5 min late",
        "thanks so much!", "let me check and get back", "on my way already",
        "perfect, sounds good", "no worries 👍", "i'll send the doc shortly",
        "lunch tomorrow?", "happy birthday!!", "is the meeting still on",
        "got it", "👍", "sure thing", "haha that's wild", "call me when free",
        "ok will do", "see ya", "thanks again",
    ]

    private let times = [
        "9:14 AM", "10:02 AM", "11:48 AM", "12:33 PM", "1:21 PM", "2:09 PM",
        "3:46 PM", "4:18 PM", "5:55 PM", "Yesterday", "Mon", "Sun", "Sat",
    ]

    private func initials(for name: String) -> String {
        let parts = name.split(separator: " ")
        if parts.count >= 2 {
            return String(parts[0].prefix(1)) + String(parts[1].prefix(1))
        }
        return String(name.prefix(2)).uppercased()
    }

    private func avatarColor(for name: String) -> Color {
        // Hash name into a stable hue so avatars vary but reproduce.
        var hash: UInt64 = 0
        for ch in name.unicodeScalars { hash = hash &* 31 &+ UInt64(ch.value) }
        let palette: [Color] = [.blue, .green, .orange, .purple, .pink, .teal, .indigo, .brown]
        return palette[Int(hash % UInt64(palette.count))]
    }
}
