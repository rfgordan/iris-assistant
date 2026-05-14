import SwiftUI

/// Target circle plus N distractor circles. Agent must tap the target.
///
/// Launch args:
///   -DistractorN <int>      number of distractors (default 3)
///   -Size <int>             circle diameter in points (default 60)
///   -Seed <int>             RNG seed for positions (default 1)
///   -TargetColor <name>     default "red"
///   -DistractorColor <name> default "gray"
///   -TimeoutS <double>      default 30
struct TapWithDistractorsView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var size: CGFloat { CGFloat(config.int("Size", default: 60)) }
    private var distractorN: Int { config.int("DistractorN", default: 3) }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 30) }
    private var targetColorName: String { config.string("TargetColor", default: "red") }
    private var distractorColorName: String { config.string("DistractorColor", default: "gray") }

    private var params: [String: String] {
        [
            "size": "\(Int(size))",
            "distractor_n": "\(distractorN)",
            "seed": "\(seed)",
            "target_color": targetColorName,
            "distractor_color": distractorColorName,
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let positions = nonOverlappingPositions(in: geo.size, count: distractorN + 1)
            let target = colorFromString(targetColorName)
            let distractor = colorFromString(distractorColorName)
            ZStack {
                Color.white
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(coordinateSpace: .local) { location in
                        handleTap(at: location, positions: positions)
                    }

                // Render circles as a single non-interactive drawing layer
                // so SwiftUI's hit-test never has to walk past N positioned
                // sub-views to reach the Color underneath.
                Canvas { ctx, _ in
                    for (idx, pos) in positions.enumerated() {
                        let rect = CGRect(
                            x: pos.x - size / 2,
                            y: pos.y - size / 2,
                            width: size,
                            height: size
                        )
                        ctx.fill(Path(ellipseIn: rect), with: .color(idx == 0 ? target : distractor))
                    }
                }
                .allowsHitTesting(false)
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("tap_with_distractors appeared n=\(distractorN)")
        }
    }

    private func nonOverlappingPositions(in canvas: CGSize, count: Int) -> [CGPoint] {
        var rng = SeededRNG(seed: UInt64(seed))
        let margin: CGFloat = size / 2 + 20
        let minDist = size + 10
        var out: [CGPoint] = []
        var tries = 0
        while out.count < count && tries < 1000 {
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
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y)))")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        let r = size / 2

        // Find the closest circle within hit radius.
        var hitIndex: Int? = nil
        var bestDist: CGFloat = .greatestFiniteMagnitude
        for (i, pos) in positions.enumerated() {
            let d = hypot(point.x - pos.x, point.y - pos.y)
            if d <= r && d < bestDist {
                bestDist = d
                hitIndex = i
            }
        }

        if hitIndex == 0 {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "tap_with_distractors", params: params, elapsed: elapsed)
        } else {
            let reason = hitIndex == nil ? "wrong_tap" : "tapped_distractor"
            Signal.fail(
                scenario: "tap_with_distractors",
                params: params,
                reason: reason,
                elapsed: elapsed,
                extras: [
                    "tap_x": "\(Int(point.x))",
                    "tap_y": "\(Int(point.y))",
                    "hit_index": hitIndex.map { "\($0)" } ?? "none",
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
                Signal.fail(scenario: "tap_with_distractors", params: params, reason: "timeout", elapsed: elapsed)
            }
        }
    }
}
