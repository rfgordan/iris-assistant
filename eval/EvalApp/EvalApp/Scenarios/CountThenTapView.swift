import SwiftUI

/// Field of N circles, some red and some gray. Bottom of screen shows
/// numbered tiles 1..M. The agent must count the RED circles and tap the
/// tile matching that count.
///
/// Why it's hard for a VLM:
///   - Counting many items is a documented VLM weakness, especially when
///     items are intermixed (red and gray in the same field).
///   - The agent has to do counting + selection: get the count wrong by
///     one and the wrong tile is tapped.
///   - With NumCircles ≥ 15 the counting load increases substantially.
///   - Tiles at the bottom have small labels (FontSize ~22) requiring OCR.
///
/// Launch args:
///   -NumCircles <int>     total circles in the field (default 12)
///   -RedCount <int>       number of red circles (capped to NumCircles, default 5)
///   -CircleSize <int>     diameter pt (default 40)
///   -TileCount <int>      number of tiles to show (default 12)
///   -Seed <int>           layout RNG (default 1)
///   -TimeoutS <double>    default 60
struct CountThenTapView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var numCircles: Int { max(1, config.int("NumCircles", default: 12)) }
    private var redCount: Int { min(numCircles, max(0, config.int("RedCount", default: 5))) }
    private var circleSize: CGFloat { CGFloat(config.int("CircleSize", default: 40)) }
    private var tileCount: Int { max(1, config.int("TileCount", default: 12)) }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 60) }

    private var params: [String: String] {
        [
            "num_circles": "\(numCircles)",
            "red_count": "\(redCount)",
            "circle_size": "\(Int(circleSize))",
            "tile_count": "\(tileCount)",
            "seed": "\(seed)",
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let bottomBand: CGFloat = 110
            let fieldHeight = geo.size.height - bottomBand - 60
            let layout = layoutCircles(in: CGSize(width: geo.size.width, height: fieldHeight))
            let tileW = geo.size.width / CGFloat(tileCount)
            let tileY = geo.size.height - bottomBand / 2

            ZStack {
                Color.white
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(coordinateSpace: .local) { loc in
                        handleTap(at: loc, geo: geo, bottomBand: bottomBand)
                    }

                // All visuals drawn in a single non-interactive layer so
                // hit-testing isn't blocked by stacked .position'd views.
                Canvas { ctx, _ in
                    // Circles
                    for (pos, isRed) in layout {
                        let rect = CGRect(
                            x: pos.x - circleSize / 2,
                            y: pos.y - circleSize / 2,
                            width: circleSize, height: circleSize
                        )
                        ctx.fill(Path(ellipseIn: rect),
                                 with: .color(isRed ? .red : Color(white: 0.6)))
                    }
                    // Tile backgrounds
                    for i in 0..<tileCount {
                        let cx = (CGFloat(i) + 0.5) * tileW
                        let rect = CGRect(
                            x: cx - (tileW - 6) / 2,
                            y: tileY - 35,
                            width: tileW - 6, height: 70
                        )
                        let path = Path(roundedRect: rect, cornerRadius: 8)
                        ctx.fill(path, with: .color(Color(white: 0.95)))
                        ctx.stroke(path, with: .color(.black.opacity(0.5)), lineWidth: 1.5)
                        // Label
                        let text = Text("\(i + 1)")
                            .font(.system(size: 22, weight: .semibold))
                            .foregroundColor(.black)
                        ctx.draw(text, at: CGPoint(x: cx, y: tileY), anchor: .center)
                    }
                }
                .allowsHitTesting(false)
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("count_then_tap appeared n=\(numCircles) red=\(redCount)")
        }
    }

    private func layoutCircles(in size: CGSize) -> [(CGPoint, Bool)] {
        var rng = SeededRNG(seed: UInt64(seed))
        let margin: CGFloat = circleSize / 2 + 12
        let minDist = circleSize + 8
        var positions: [CGPoint] = []
        var tries = 0
        while positions.count < numCircles && tries < 5000 {
            tries += 1
            let x = CGFloat.random(in: margin...(size.width - margin), using: &rng)
            let y = CGFloat.random(in: (margin + 60)...(size.height - margin), using: &rng)
            let p = CGPoint(x: x, y: y)
            if positions.allSatisfy({ hypot($0.x - p.x, $0.y - p.y) >= minDist }) {
                positions.append(p)
            }
        }
        // Shuffle deterministically and label first redCount as red.
        var indices = Array(0..<positions.count)
        for i in stride(from: indices.count - 1, to: 0, by: -1) {
            let j = Int.random(in: 0...i, using: &rng)
            indices.swapAt(i, j)
        }
        var isRed = Array(repeating: false, count: positions.count)
        for k in 0..<min(redCount, positions.count) {
            isRed[indices[k]] = true
        }
        return positions.enumerated().map { ($0.element, isRed[$0.offset]) }
    }

    private func handleTap(at point: CGPoint, geo: GeometryProxy, bottomBand: CGFloat) {
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y)))")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)

        // Tap must be in the tile band at the bottom.
        let bandTop = geo.size.height - bottomBand
        guard point.y >= bandTop else {
            completed = true
            Signal.fail(
                scenario: "count_then_tap", params: params,
                reason: "wrong_tap", elapsed: elapsed,
                extras: ["tap_x": "\(Int(point.x))", "tap_y": "\(Int(point.y))"]
            )
            return
        }
        let tileW = geo.size.width / CGFloat(tileCount)
        let tileIndex = Int(point.x / tileW)
        let tappedNumber = tileIndex + 1
        completed = true
        if tappedNumber == redCount {
            timeoutTask?.cancel()
            Signal.pass(scenario: "count_then_tap", params: params, elapsed: elapsed)
        } else {
            Signal.fail(
                scenario: "count_then_tap", params: params,
                reason: "wrong_count", elapsed: elapsed,
                extras: [
                    "tapped_number": "\(tappedNumber)",
                    "correct_count": "\(redCount)",
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
                    scenario: "count_then_tap", params: params,
                    reason: "timeout", elapsed: elapsed
                )
            }
        }
    }
}
