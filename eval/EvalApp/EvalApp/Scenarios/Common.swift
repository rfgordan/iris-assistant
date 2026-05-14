import Foundation
import SwiftUI

/// Deterministic seeded PRNG so scenarios with random layouts reproduce.
struct SeededRNG: RandomNumberGenerator {
    var state: UInt64
    init(seed: UInt64) { self.state = seed &+ 0x9E3779B97F4A7C15 }
    mutating func next() -> UInt64 {
        state = state &+ 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z &>> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z &>> 27)) &* 0x94D049BB133111EB
        return z ^ (z &>> 31)
    }
}

func colorFromString(_ s: String) -> Color {
    switch s {
    case "blue":  return .blue
    case "green": return .green
    case "gray":  return Color(white: 0.6)
    case "yellow": return .yellow
    case "orange": return .orange
    case "purple": return .purple
    default:      return .red
    }
}
