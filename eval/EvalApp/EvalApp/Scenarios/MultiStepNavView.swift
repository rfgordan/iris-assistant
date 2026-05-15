import SwiftUI

/// N intermediate screens, each with a "Continue" button. After the agent
/// taps Continue NavDepth times it reaches a target screen with a circle.
/// Tapping the target passes; tapping the background on the target screen
/// fails with wrong_tap.
///
/// Launch args:
///   -NavDepth <int>         number of Continue taps before the target (default 1)
///   -Size <int>             target diameter on the final screen (default 80)
///   -TargetColor <name>     default "red"
///   -TimeoutS <double>      default 30
struct MultiStepNavView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var step: Int = 0
    @State private var timeoutTask: Task<Void, Never>?

    private var navDepth: Int { config.int("NavDepth", default: 1) }
    private var size: CGFloat { CGFloat(config.int("Size", default: 80)) }
    private var timeoutS: Double { config.double("TimeoutS", default: 30) }
    private var targetColorName: String { config.string("TargetColor", default: "red") }

    private var params: [String: String] {
        [
            "nav_depth": "\(navDepth)",
            "size": "\(Int(size))",
            "target_color": targetColorName,
        ]
    }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Color.white.ignoresSafeArea()

                if step < navDepth {
                    navScreen
                } else {
                    targetScreen(in: geo.size)
                }
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("multi_step_nav appeared depth=\(navDepth)")
        }
    }

    @ViewBuilder
    private var navScreen: some View {
        VStack(spacing: 24) {
            Text("Step \(step + 1) of \(navDepth + 1)")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("Tap Continue to advance.")
                .font(.body)
                .foregroundStyle(.secondary)
            Button {
                guard !completed else { return }
                step += 1
                Signal.debug("nav advanced to step=\(step)")
            } label: {
                Text("Continue")
                    .font(.title2.weight(.semibold))
                    .padding(.horizontal, 32)
                    .padding(.vertical, 14)
                    .background(Color.blue, in: Capsule())
                    .foregroundStyle(.white)
            }
        }
    }

    @ViewBuilder
    private func targetScreen(in canvas: CGSize) -> some View {
        let targetCenter = CGPoint(x: canvas.width / 2, y: canvas.height / 2)
        ZStack {
            Color.white
                .contentShape(Rectangle())
                .onTapGesture(coordinateSpace: .local) { location in
                    handleTap(at: location, target: targetCenter)
                }
            Circle()
                .fill(colorFromString(targetColorName))
                .frame(width: size, height: size)
                .position(targetCenter)
                .allowsHitTesting(false)
            Text("Tap the circle")
                .font(.callout)
                .foregroundStyle(.secondary)
                .position(x: canvas.width / 2, y: 60)
                .allowsHitTesting(false)
        }
    }

    private func handleTap(at point: CGPoint, target: CGPoint) {
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y)))")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        let dist = hypot(point.x - target.x, point.y - target.y)

        if dist <= size / 2 {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "multi_step_nav", params: params, elapsed: elapsed)
        } else {
            Signal.fail(
                scenario: "multi_step_nav",
                params: params,
                reason: "wrong_tap",
                elapsed: elapsed,
                extras: [
                    "tap_x": "\(Int(point.x))",
                    "tap_y": "\(Int(point.y))",
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
                Signal.fail(scenario: "multi_step_nav", params: params, reason: "timeout", elapsed: elapsed)
            }
        }
    }
}
