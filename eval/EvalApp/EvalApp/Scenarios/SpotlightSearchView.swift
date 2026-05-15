import SwiftUI

/// Spotlight-style search: small "Search" pill near the bottom of the
/// screen. Tapping it expands to a search modal with a text field and a
/// filtered results list. Agent must:
///   1. Tap the search pill (small target — not full-width)
///   2. Type at least MinQueryLength characters
///   3. Tap the matching result row
///
/// Realism notes:
///   - The pill is ~150pt wide × 36pt tall, centered horizontally.
///   - The catalog has ~40 items with realistic-app-like names so
///     filtering produces a sensible result list.
///   - We deliberately do NOT auto-select the first result — the agent
///     has to tap it explicitly, just like real Spotlight.
///
/// Launch args:
///   -ItemCount <int>       items in the catalog (default 40)
///   -TargetName <string>   target app/item name (default "Weather")
///   -MinQueryLength <int>  min chars before filter activates (default 3)
///   -Seed <int>            reserved (catalog is deterministic for now)
///   -TimeoutS <double>     default 60
struct SpotlightSearchView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?
    @State private var searchOpen: Bool = false
    @State private var query: String = ""
    @FocusState private var searchFocused: Bool

    private var itemCount: Int { max(5, config.int("ItemCount", default: 40)) }
    private var targetName: String { config.string("TargetName", default: "Weather") }
    private var minQueryLength: Int { max(1, config.int("MinQueryLength", default: 3)) }
    private var timeoutS: Double { config.double("TimeoutS", default: 60) }
    private var seed: Int { config.int("Seed", default: 1) }

    private var params: [String: String] {
        [
            "item_count": "\(itemCount)",
            "target_name": targetName,
            "min_query_length": "\(minQueryLength)",
            "seed": "\(seed)",
        ]
    }

    private var catalog: [String] {
        // Pool of plausible app names; truncated to itemCount.
        let pool = [
            "Weather", "Notes", "Reminders", "Calendar", "Calculator", "Camera",
            "Messages", "Mail", "Maps", "Phone", "FaceTime", "Music", "Podcasts",
            "Photos", "Files", "Wallet", "Health", "Fitness", "Home", "Shortcuts",
            "Books", "Stocks", "News", "TV", "Translate", "Voice Memos", "Clock",
            "Compass", "Magnifier", "Measure", "Find My", "Settings", "Safari",
            "Contacts", "App Store", "Tips", "Watch", "Sandbox", "Wallet Pro",
            "Weatherwise", "Notepad", "Reminders Pro", "Calendly", "Calmly",
            "Cameraman", "Messenger", "Mailbox", "Mapper", "Music Studio",
        ]
        return Array(pool.prefix(itemCount))
    }

    private var filtered: [String] {
        if query.count < minQueryLength { return [] }
        let q = query.lowercased()
        return catalog.filter { $0.lowercased().contains(q) }
    }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                // Base screen: a dim wallpaper-ish background.
                LinearGradient(
                    colors: [Color(white: 0.92), Color(white: 0.82)],
                    startPoint: .top, endPoint: .bottom
                ).ignoresSafeArea()

                if !searchOpen {
                    Text("Home")
                        .font(.largeTitle.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .position(x: geo.size.width / 2, y: 80)

                    // The search pill: deliberately small so it's a precision-tap target.
                    searchPill
                        .position(x: geo.size.width / 2, y: geo.size.height - 80)
                }

                if searchOpen {
                    searchModal(in: geo.size)
                }
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("spotlight_search appeared target=\(targetName) items=\(itemCount)")
        }
    }

    @ViewBuilder
    private var searchPill: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
            Text("Search")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial, in: Capsule())
        .frame(width: 160, height: 36)
        .contentShape(Capsule())
        .onTapGesture {
            guard !completed else { return }
            searchOpen = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                searchFocused = true
            }
            Signal.debug("search opened")
        }
    }

    @ViewBuilder
    private func searchModal(in canvas: CGSize) -> some View {
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search", text: $query)
                    .focused($searchFocused)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled(true)
                    .font(.body)
                if !query.isEmpty {
                    Button {
                        query = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color(white: 0.95), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal, 16)
            .padding(.top, 60)

            if query.count < minQueryLength {
                Spacer()
                Text("Type at least \(minQueryLength) characters")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
            } else if filtered.isEmpty {
                Spacer()
                Text("No results for \"\(query)\"")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
            } else {
                List(filtered, id: \.self) { name in
                    Button {
                        handleResultTap(name: name)
                    } label: {
                        HStack(spacing: 12) {
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color(white: 0.75))
                                .frame(width: 28, height: 28)
                            Text(name).font(.body).foregroundStyle(.primary)
                            Spacer()
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .background(Color.white.ignoresSafeArea())
    }

    private func handleResultTap(name: String) {
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        if name == targetName {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(
                scenario: "spotlight_search", params: params, elapsed: elapsed
            )
        } else {
            completed = true
            timeoutTask?.cancel()
            Signal.fail(
                scenario: "spotlight_search", params: params,
                reason: "wrong_result", elapsed: elapsed,
                extras: ["tapped": name, "query": query]
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
                    scenario: "spotlight_search", params: params,
                    reason: "timeout", elapsed: elapsed,
                    extras: ["query": query, "search_open": "\(searchOpen)"]
                )
            }
        }
    }
}
