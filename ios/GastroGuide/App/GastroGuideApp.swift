import SwiftUI

@main struct GastroGuideApp: App {
    @StateObject private var session = AppState()
    var body: some Scene {
        WindowGroup {
            Group {
                if session.isAuthenticated {
                    TabView {
                        NavigationStack { RecommendationView() }.tabItem { Label("Подобрать", systemImage: "sparkles") }
                        NavigationStack { RestaurantsView() }.tabItem { Label("Рестораны", systemImage: "fork.knife") }
                        NavigationStack { DiaryView() }.tabItem { Label("Дневник", systemImage: "list.bullet.clipboard") }
                        NavigationStack { ProfileView() }.tabItem { Label("Профиль", systemImage: "person.crop.circle") }
                    }
                } else {
                    NavigationStack { LoginView() }
                }
            }
            .environmentObject(session)
            .tint(.green)
            .task(id: session.isAuthenticated) {
                if session.isAuthenticated, let client = try? session.client() {
                    do { session.received(profile: try await client.profile()) }
                    catch { _ = session.report(error) }
                }
            }
        }
    }
}
