import SwiftUI

/// G×G grid of near-identical circles. One cell differs by a configurable
/// amount in either color (hue shift) or size. Agent must tap that cell.
///
/// Why it's hard for a VLM:
///   - With OddDelta ≤ 0.05 the difference is at the limit of pixel-level
///     perception in a downsampled screenshot.
///   - With G≥4 the candidate set is large (≥16), making exhaustive
///     comparison expensive in the model's attention.
///   - The agent can't pick the "stand-out" by saliency tricks — every
///     circle looks similar.
///
/// Launch args:
///   -GridSize <int>      grid dimension (default 4 → 16 cells)
///   -OddDim <color|size> which axis the odd cell differs on (default color)
///   -OddDelta <double>   magnitude of difference, 0..1 (default 0.15)
///   -Size <int>          base circle diameter pt (default 60)
///   -Seed <int>          which cell is odd (default 1)
///   -TimeoutS <double>   default 60
struct FindOddOneView: View {
    let config: ScenarioConfig
    @State private var startedAt: Date = Date()
    @State private var completed: Bool = false
    @State private var timeoutTask: Task<Void, Never>?

    private var gridSize: Int { max(2, config.int("GridSize", default: 4)) }
    private var oddDim: String { config.string("OddDim", default: "color") }
    private var oddDelta: Double { config.double("OddDelta", default: 0.15) }
    private var size: CGFloat { CGFloat(config.int("Size", default: 60)) }
    private var seed: Int { config.int("Seed", default: 1) }
    private var timeoutS: Double { config.double("TimeoutS", default: 60) }

    private var oddCell: Int {
        var rng = SeededRNG(seed: UInt64(seed))
        return Int.random(in: 0..<(gridSize * gridSize), using: &rng)
    }

    private var params: [String: String] {
        [
            "grid_size": "\(gridSize)",
            "odd_dim": oddDim,
            "odd_delta": String(format: "%.2f", oddDelta),
            "size": "\(Int(size))",
            "seed": "\(seed)",
        ]
    }

    var body: some View {
        GeometryReader { geo in
            let topReserve: CGFloat = 80
            let cellW = geo.size.width / CGFloat(gridSize)
            let cellH = max(60, (geo.size.height - topReserve) / CGFloat(gridSize))
            let oc = oddCell
            ZStack {
                Color.white
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(coordinateSpace: .local) { location in
                        handleTap(at: location, cellW: cellW, cellH: cellH,
                                  topReserve: topReserve, oddCell: oc)
                    }

                ForEach(0..<(gridSize * gridSize), id: \.self) { i in
                    let row = i / gridSize
                    let col = i % gridSize
                    let cx = (CGFloat(col) + 0.5) * cellW
                    let cy = topReserve + (CGFloat(row) + 0.5) * cellH
                    let isOdd = i == oc
                    let drawnSize = (oddDim == "size" && isOdd)
                        ? size * CGFloat(1.0 + oddDelta)
                        : size
                    let baseColor = Color(red: 0.30, green: 0.50, blue: 0.85)
                    let drawnColor = (oddDim == "color" && isOdd)
                        ? Color(red: 0.30 + oddDelta, green: 0.50, blue: 0.85 - oddDelta * 0.5)
                        : baseColor
                    Circle()
                        .fill(drawnColor)
                        .frame(width: drawnSize, height: drawnSize)
                        .position(x: cx, y: cy)
                        .allowsHitTesting(false)
                }
            }
        }
        .onAppear {
            startedAt = Date()
            scheduleTimeout()
            Signal.debug("find_odd_one appeared grid=\(gridSize) dim=\(oddDim) delta=\(oddDelta) odd=\(oddCell)")
        }
    }

    private func handleTap(at point: CGPoint, cellW: CGFloat, cellH: CGFloat,
                           topReserve: CGFloat, oddCell: Int) {
        Signal.debug("handleTap point=(\(Int(point.x)),\(Int(point.y)))")
        guard !completed else { return }
        let elapsed = Date().timeIntervalSince(startedAt)

        if point.y < topReserve {
            completed = true
            Signal.fail(
                scenario: "find_odd_one", params: params,
                reason: "wrong_tap", elapsed: elapsed,
                extras: ["tap_x": "\(Int(point.x))", "tap_y": "\(Int(point.y))"]
            )
            return
        }

        let col = Int(point.x / cellW)
        let row = Int((point.y - topReserve) / cellH)
        if col < 0 || col >= gridSize || row < 0 || row >= gridSize {
            completed = true
            Signal.fail(
                scenario: "find_odd_one", params: params,
                reason: "wrong_tap", elapsed: elapsed,
                extras: ["tap_x": "\(Int(point.x))", "tap_y": "\(Int(point.y))"]
            )
            return
        }
        let cellIdx = row * gridSize + col
        completed = true
        if cellIdx == oddCell {
            timeoutTask?.cancel()
            Signal.pass(scenario: "find_odd_one", params: params, elapsed: elapsed)
        } else {
            Signal.fail(
                scenario: "find_odd_one", params: params,
                reason: "wrong_cell", elapsed: elapsed,
                extras: [
                    "tapped_cell": "\(cellIdx)",
                    "odd_cell": "\(oddCell)",
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
                    scenario: "find_odd_one", params: params,
                    reason: "timeout", elapsed: elapsed
                )
            }
        }
    }
}
