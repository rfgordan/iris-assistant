import SwiftUI

/// iOS-Home-Screen-like horizontal paged scroll view of "app icon" tiles.
/// Agent must horizontally page (swipe or hscroll) until the target tile
/// is visible, then tap it. There are no chevrons or page-jump buttons —
/// horizontal navigation is the input being tested.
///
/// Realism notes:
///   - 4×6 grid per page = 24 icons. Icon ~64pt rounded squares + 11pt
///     label. Matches iOS Home Screen density.
///   - Page indicator dots at bottom are informational only; they do not
///     accept taps to jump pages.
///   - Snapped paging via .scrollTargetBehavior(.paging).
///
/// Launch args:
///   -PageCount <int>      number of pages (default 4)
///   -IconsPerPage <int>   icons per page (default 24)
///   -TargetLabel <string> target icon label (default "Weather")
///   -TargetPage <int>     1-indexed page containing target (default 3)
///   -Seed <int>           shuffles fillers; target slot is deterministic
///   -TimeoutS <double>    default 90
struct SwipePagesFindView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var pageCount: Int { max(2, config.int("PageCount", default: 4)) }
    private var iconsPerPage: Int { max(4, config.int("IconsPerPage", default: 24)) }
    private var targetLabel: String { config.string("TargetLabel", default: "Weather") }
    private var targetPage: Int { max(1, min(pageCount, config.int("TargetPage", default: 3))) }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 90) }

    private var params: [String: String] {
        [
            "page_count": "\(pageCount)",
            "icons_per_page": "\(iconsPerPage)",
            "target_label": targetLabel,
            "target_page": "\(targetPage)",
            "seed": "\(seed)",
        ]
    }

    private var pages: [[IconTile]] {
        var rng = SeededRNG(seed: UInt64(seed))
        var labels = filler
        // Shuffle deterministically.
        for i in stride(from: labels.count - 1, to: 0, by: -1) {
            let j = Int.random(in: 0...i, using: &rng)
            labels.swapAt(i, j)
        }
        // Remove any accidental target name collisions from the filler pool.
        labels.removeAll { $0 == targetLabel }

        var pageList: [[IconTile]] = []
        var cursor = 0
        for pageIdx in 0..<pageCount {
            var icons: [IconTile] = []
            for _ in 0..<iconsPerPage {
                let label = labels[cursor % labels.count]
                cursor += 1
                icons.append(IconTile(label: label, color: colorFor(label)))
            }
            // Insert target into a deterministic slot of the target page.
            if pageIdx == targetPage - 1 {
                // Place target at a fixed slot index for reproducibility.
                let slot = Int(rng.next() % UInt64(iconsPerPage))
                icons[slot] = IconTile(label: targetLabel, color: colorFor(targetLabel))
            }
            pageList.append(icons)
        }
        return pageList
    }

    var body: some View {
        GeometryReader { geo in
            VStack(spacing: 0) {
                Text("Apps")
                    .font(.system(size: 22, weight: .semibold))
                    .padding(.top, 12)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 0) {
                        ForEach(Array(pages.enumerated()), id: \.offset) { pIdx, icons in
                            iconGrid(for: icons, pageWidth: geo.size.width)
                                .frame(width: geo.size.width)
                                .id(pIdx)
                        }
                    }
                    .scrollTargetLayout()
                }
                .scrollTargetBehavior(.paging)

                // Page indicator dots (informational only — not tappable).
                HStack(spacing: 8) {
                    ForEach(0..<pageCount, id: \.self) { _ in
                        Circle()
                            .fill(Color(white: 0.6))
                            .frame(width: 6, height: 6)
                    }
                }
                .padding(.bottom, 24)
                .allowsHitTesting(false)
            }
            .background(
                LinearGradient(
                    colors: [Color(white: 0.92), Color(white: 0.82)],
                    startPoint: .top, endPoint: .bottom
                ).ignoresSafeArea()
            )
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("swipe_pages_find appeared target=\(targetLabel) page=\(targetPage)")
        }
    }

    @ViewBuilder
    private func iconGrid(for icons: [IconTile], pageWidth: CGFloat) -> some View {
        let columns = 4
        let cols = Array(repeating: GridItem(.flexible(), spacing: 16), count: columns)
        ScrollView(.vertical, showsIndicators: false) {
            LazyVGrid(columns: cols, spacing: 16) {
                ForEach(Array(icons.enumerated()), id: \.offset) { _, tile in
                    Button {
                        handleIconTap(label: tile.label)
                    } label: {
                        VStack(spacing: 4) {
                            RoundedRectangle(cornerRadius: 14)
                                .fill(tile.color)
                                .frame(width: 60, height: 60)
                                .overlay(
                                    Text(String(tile.label.prefix(1)))
                                        .font(.system(size: 22, weight: .bold))
                                        .foregroundStyle(.white)
                                )
                            Text(tile.label)
                                .font(.system(size: 11))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                                .frame(width: 70)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
        }
        .frame(width: pageWidth)
    }

    private func handleIconTap(label: String) {
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        if label == targetLabel {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "swipe_pages_find", params: params, elapsed: elapsed)
        } else {
            completed = true
            timeoutTask?.cancel()
            Signal.fail(
                scenario: "swipe_pages_find", params: params,
                reason: "wrong_icon", elapsed: elapsed,
                extras: ["tapped": label]
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
                Signal.fail(scenario: "swipe_pages_find", params: params, reason: "timeout", elapsed: elapsed)
            }
        }
    }

    // MARK: - Filler tiles

    private struct IconTile { let label: String; let color: Color }

    private var filler: [String] {
        [
            "Notes", "Mail", "Calendar", "Camera", "Calculator", "Maps",
            "Music", "Photos", "Files", "Wallet", "Health", "Fitness",
            "Home", "Shortcuts", "Books", "Stocks", "News", "TV",
            "Translate", "Voice Memos", "Clock", "Compass", "Magnifier",
            "Measure", "Find My", "Settings", "Safari", "Contacts",
            "App Store", "Tips", "Watch", "Reminders", "Phone", "Messages",
            "FaceTime", "Music Studio", "Podcasts", "Mapper", "Cameraman",
            "Notepad", "Calendly", "Wallet Pro", "Mailbox", "Messenger",
            "Calmly", "Reminders Pro", "Weatherwise", "Sandbox",
            "Beacon", "Drift", "Flux", "Glow", "Helix", "Inkwell", "Jot",
            "Kindly", "Loom", "Mango", "Nimbus", "Orbit", "Pulse",
            "Quill", "Ripple", "Scribe", "Tilt", "Umbra", "Vista",
            "Whisp", "Xenon", "Yarn", "Zest",
        ]
    }

    private func colorFor(_ label: String) -> Color {
        var hash: UInt64 = 0
        for ch in label.unicodeScalars { hash = hash &* 31 &+ UInt64(ch.value) }
        let palette: [Color] = [.blue, .green, .orange, .purple, .pink, .teal, .indigo, .red, .brown, .cyan]
        return palette[Int(hash % UInt64(palette.count))]
    }
}
