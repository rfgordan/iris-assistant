import SwiftUI

/// N numbered circles scrambled across the screen. Agent must tap them in
/// numerical order (1, 2, 3, ..., N). Wrong order = hard fail.
///
/// Why it's hard for a VLM:
///   - OCR of small labels on circles, especially with FontSize < 24.
///   - Each step requires holding state about which circle is next.
///   - Wrong order fails immediately — no exploratory tapping.
///   - With Count >= 8 the agent has to enumerate many candidates.
///
/// Launch args:
///   -Count <int>          number of circles (default 5)
///   -Size <int>           circle diameter pt (default 80)
///   -FontSize <int>       label font size pt (default 28)
///   -Seed <int>           position RNG (default 1)
///   -TimeoutS <double>    default 60
struct TapInOrderView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var nextExpected: Int = 1
    @State private var timeoutTask: Task<Void, Never>?

    private var count: Int { config.int("Count", default: 5) }
    private var size: CGFloat { CGFloat(config.int("Size", default: 80)) }
    private var fontSize: CGFloat { CGFloat(config.int("FontSize", default: 28)) }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 60) }

    private var params: [String: String] {
        [
            "count": "\(count)",
            "size": "\(Int(size))",
            "font_size": "\(Int(fontSize))",
            "seed": "\(seed)",
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let positions = nonOverlappingPositions(in: geo.size, count: count)
            ZStack {
                Color.white
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(coordinateSpace: .local) { location in
                        handleTap(at: location, positions: positions)
                    }

                ForEach(0..<positions.count, id: \.self) { idx in
                    let pos = positions[idx]
                    let n = idx + 1
                    let done = n < nextExpected
                    ZStack {
                        Circle()
                            .fill(done ? Color.gray.opacity(0.35) : Color.blue)
                            .frame(width: size, height: size)
                        Text("\(n)")
                            .font(.system(size: fontSize, weight: .bold))
                            .foregroundColor(.white)
                    }
                    .position(pos)
                    .allowsHitTesting(false)
                }
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("tap_in_order appeared count=\(count)")
        }
    }

    private func nonOverlappingPositions(in canvas: CGSize, count: Int) -> [CGPoint] {
        var rng = SeededRNG(seed: UInt64(seed))
        let margin: CGFloat = size / 2 + 24
        let minDist = size + 20
        var out: [CGPoint] = []
        var tries = 0
        while out.count < count && tries < 2000 {
            tries += 1
            let x = CGFloat.random(in: margin...(canvas.width - margin), using: &rng)
            let y = CGFloat.random(in: margin...(canvas.height - margin), using: &rng)
            let p = CGPoint(x: x, y: y)
            if out.allSatisfy({ hypot($0.x - p.x, $0.y - p.y) >= minDist }) {
                out.append(p)
            }
        }
        return out
    }

    private func handleTap(at point: CGPoint, positions: [CGPoint]) {
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y))) expected=\(nextExpected)")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        let r = size / 2
        var hitIndex: Int? = nil
        var bestDist: CGFloat = .greatestFiniteMagnitude
        for (i, pos) in positions.enumerated() {
            let d = hypot(point.x - pos.x, point.y - pos.y)
            if d <= r && d < bestDist {
                bestDist = d
                hitIndex = i
            }
        }

        guard let hit = hitIndex else {
            completed = true
            Signal.fail(
                scenario: "tap_in_order", params: params, reason: "wrong_tap",
                elapsed: elapsed,
                extras: [
                    "tap_x": "\(Int(point.x))",
                    "tap_y": "\(Int(point.y))",
                    "expected": "\(nextExpected)",
                ]
            )
            return
        }

        let tappedNumber = hit + 1
        if tappedNumber == nextExpected {
            nextExpected += 1
            if nextExpected > count {
                completed = true
                timeoutTask?.cancel()
                Signal.pass(scenario: "tap_in_order", params: params, elapsed: elapsed)
            }
        } else {
            completed = true
            Signal.fail(
                scenario: "tap_in_order", params: params, reason: "wrong_order",
                elapsed: elapsed,
                extras: [
                    "expected": "\(nextExpected)",
                    "tapped": "\(tappedNumber)",
                ]
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
                    scenario: "tap_in_order", params: params, reason: "timeout",
                    elapsed: elapsed,
                    extras: ["last_expected": "\(nextExpected)"]
                )
            }
        }
    }
}
