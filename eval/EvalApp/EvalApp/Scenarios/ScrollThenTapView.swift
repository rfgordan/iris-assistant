import SwiftUI

/// Target placed below the initial viewport inside a ScrollView. The agent
/// must scroll the content first, then tap the target. Hit test is in the
/// ScrollView's content coordinate space, so it's robust to where the user
/// is scrolled when they tap.
///
/// Launch args:
///   -ScrollDistance <int>  points the target is below the initial viewport (default 600)
///   -Size <int>            target diameter (default 80)
///   -TargetColor <name>    default "red"
///   -TimeoutS <double>     default 30
struct ScrollThenTapView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var size: CGFloat { CGFloat(config.int("Size", default: 80)) }
    private var scrollDistance: CGFloat { CGFloat(config.int("ScrollDistance", default: 600)) }
    private var timeoutS: Double { config.double("TimeoutS", default: 30) }
    private var targetColorName: String { config.string("TargetColor", default: "red") }

    private var params: [String: String] {
        [
            "size": "\(Int(size))",
            "scroll_distance": "\(Int(scrollDistance))",
            "target_color": targetColorName,
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let totalHeight = geo.size.height + scrollDistance + size + 100
            let targetCenter = CGPoint(
                x: geo.size.width / 2,
                y: geo.size.height + scrollDistance + size / 2
            )
            ScrollView {
                ZStack(alignment: .topLeading) {
                    Color.white
                        .frame(width: geo.size.width, height: totalHeight)

                    Text("Scroll down to find the target")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                        .position(x: geo.size.width / 2, y: 60)
                        .allowsHitTesting(false)

                    Circle()
                        .fill(colorFromString(targetColorName))
                        .frame(width: size, height: size)
                        .position(targetCenter)
                        .allowsHitTesting(false)
                }
                .contentShape(Rectangle())
                .onTapGesture(coordinateSpace: .local) { location in
                    handleTap(at: location, target: targetCenter)
                }
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("scroll_then_tap appeared distance=\(Int(scrollDistance))")
        }
    }

    private func handleTap(at point: CGPoint, target: CGPoint) {
        Signal.debug("handleTap content=(\(Int(point.x)),\(Int(point.y))) target=(\(Int(target.x)),\(Int(target.y)))")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)
        let dist = hypot(point.x - target.x, point.y - target.y)

        if dist <= size / 2 {
            completed = true
            timeoutTask?.cancel()
            Signal.pass(scenario: "scroll_then_tap", params: params, elapsed: elapsed)
        } else {
            Signal.fail(
                scenario: "scroll_then_tap",
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
                Signal.fail(scenario: "scroll_then_tap", params: params, reason: "timeout", elapsed: elapsed)
            }
        }
    }
}
