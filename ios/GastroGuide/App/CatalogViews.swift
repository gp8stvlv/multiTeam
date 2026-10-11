import SwiftUI
import GastroAPI

struct RestaurantsView: View {
    @EnvironmentObject private var session: AppState
    @State private var restaurants: [Restaurant] = []
    @State private var loading = false
    @State private var errorMessage: String?
    var body: some View {
        List {
            if let errorMessage { ErrorText(message: errorMessage); Button("Повторить") { Task { await load() } } }
            if loading { ProgressView("Загружаем рестораны…") }
            if !loading && restaurants.isEmpty && errorMessage == nil { Text("Ресторанов пока нет.").foregroundStyle(.secondary) }
            ForEach(restaurants) { restaurant in
                NavigationLink { MenuView(restaurant: restaurant) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(restaurant.name).font(.headline)
                        Text(restaurant.address).font(.caption).foregroundStyle(.secondary)
                        Text(restaurant.cuisines.joined(separator: " · ")).font(.caption)
                        HStack {
                            if let rating = restaurant.rating { Label(String(format: "%.1f", rating), systemImage: "star.fill") }
                            if let distance = restaurant.distanceM { Text("\(Int(distance)) м по прямой") }
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .navigationTitle("Рестораны")
        .refreshable { await load() }
        .task { if restaurants.isEmpty { await load() } }
    }
    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do { restaurants = try await session.client().restaurants().items }
        catch { errorMessage = session.report(error) }
    }
}

struct MenuView: View {
    let restaurant: Restaurant
    @EnvironmentObject private var session: AppState
    @State private var menu: MenuResponse?
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var consumption: ConsumptionSelection?
    var body: some View {
        List {
            Section { Text(restaurant.address).font(.caption) }
            if loading { ProgressView("Загружаем меню…") }
            if let errorMessage { ErrorText(message: errorMessage); Button("Повторить") { Task { await load() } } }
            if let menu {
                Section("Меню · версия \(menu.menu.version)") {
                    ForEach(menu.items) { item in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack(alignment: .top) {
                                Text(item.name).font(.headline)
                                Spacer()
                                Text(DisplayValues.money(minor: item.priceMinor, currency: item.currency)).font(.subheadline)
                            }
                            if let description = item.description { Text(description).font(.caption) }
                            if let weight = item.portionWeightG { Text("Порция: \(Int(weight)) г").font(.caption) }
                            Text(item.ingredients.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary)
                            NutritionView(numbers: item.nutrition.values)
                            NutritionSourceView(facts: item.nutrition)
                            if !item.allergens.isEmpty {
                                Text("Аллергены: " + item.allergens.map { "\($0.code) (\($0.status))" }.joined(separator: ", "))
                                    .font(.caption).foregroundStyle(.orange)
                            }
                            if item.allergenInfoStatus != "complete" {
                                Text("Сведения об аллергенах неполные.").font(.caption).foregroundStyle(.orange)
                            }
                            HStack {
                                Button("Я это съел") { consumption = .init(menuItemId: item.id, name: item.name, portions: 1) }
                                    .buttonStyle(.bordered)
                                if !item.evidenceIds.isEmpty {
                                    NavigationLink("Источники") { EvidenceView(ids: item.evidenceIds) }.font(.caption)
                                }
                            }
                        }.padding(.vertical, 8)
                    }
                }
            }
        }
        .navigationTitle(restaurant.name)
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
        .sheet(item: $consumption) { ConsumptionSheet(selection: $0) }
    }
    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do { menu = try await session.client().menu(restaurantId: restaurant.id) }
        catch { errorMessage = session.report(error) }
    }
}
