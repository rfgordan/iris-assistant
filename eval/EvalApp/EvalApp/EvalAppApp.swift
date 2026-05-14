import SwiftUI

@main
struct EvalAppApp: App {
    var body: some Scene {
        WindowGroup {
            ScenarioRouter(config: ScenarioConfig())
                .statusBarHidden(true)
                .persistentSystemOverlays(.hidden)
        }
    }
}
