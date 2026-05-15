import SwiftUI

/// Single red target on a plain background. The agent must tap it.
///
/// Launch args:
///   -Size <int>            target diameter in points (default 80)
///   -PositionMode <fixed|random>  default "fixed"
///   -TargetX <int>         used when PositionMode == fixed (default: screen center)
///   -TargetY <int>         used when PositionMode == fixed (default: screen center)
///   -Seed <int>            used when PositionMode == random (default 1)
///   -TimeoutS <double>     auto-fail after this many seconds (default 30)
///   -BgColor <white|gray>  default "white"
///   -TargetColor <red|blue|green>  default "red"
struct TapTargetView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var size: CGFloat { CGFloat(config.int("Size", default: 80)) }
    private var positionMode: String { config.string("PositionMode", default: "fixed") }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 30) }
    private var bgColor: Color {
        config.string("BgColor", default: "white") == "gray" ? Color(white: 0.7) : .white
    }
    private var targetColor: Color {
        switch config.string("TargetColor", default: "red") {
        case "blue": return .blue
        case "green": return .green
        default: return .red
        }
    }

    private var params: [String: String] {
        [
            "size": "\(Int(size))",
            "position_mode": positionMode,
            "seed": "\(seed)",
            "bg": config.string("BgColor", default: "white"),
            "target_color": config.string("TargetColor", default: "red"),
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let pos = targetPosition(in: geo.size)
            ZStack {
                bgColor
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(coordinateSpace: .local) { location in
                        handleTap(at: location, target: pos)
                    }

                Circle()
                    .fill(targetColor)
                    .frame(width: size, height: size)
                    .position(pos)
                    .allowsHitTesting(false)
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("view appeared")
        }
    }

    private func targetPosition(in size: CGSize) -> CGPoint {
        if positionMode == "random" {
            // Deterministic per-seed pseudo-random position with safe margin.
            var rng = SeededRNG(seed: UInt64(seed))
            let margin = self.size / 2 + 20
            let x = CGFloat.random(in: margin...(size.width - margin), using: &rng)
            let y = CGFloat.random(in: margin...(size.height - margin), using: &rng)
            return CGPoint(x: x, y: y)
        }
        let defaultX = Int(size.width / 2)
        let defaultY = Int(size.height / 2)
        return CGPoint(
            x: CGFloat(config.int("TargetX", default: defaultX)),
            y: CGFloat(config.int("TargetY", default: defaultY))
        )
    }

    private func handleTap(at point: CGPoint, target: CGPoint) {
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y))) target=(\(Int(target.x)),\(Int(target.y)))")
        guard !completed else { return }
        let dx = point.x - target.x
        let dy = point.y - target.y
        let dist = sqrt(dx * dx + dy * dy)
        let elapsed = Date().timeIntervalSince(startedAt)

        if dist <= size / 2 {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "tap_target", params: params, elapsed: elapsed)
        } else {
            // Wrong tap — record but don't auto-fail; the agent might recover.
            Signal.fail(
                scenario: "tap_target",
                params: params,
                reason: "wrong_tap",
                elapsed: elapsed,
                extras: [
                    "tap_x": "\(Int(point.x))",
                    "tap_y": "\(Int(point.y))",
                    "dist": String(format: "%.1f", dist),
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
                Signal.fail(scenario: "tap_target", params: params, reason: "timeout", elapsed: elapsed)
            }
        }
    }
}

