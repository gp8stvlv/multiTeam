import XCTest
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import GastroAPI

final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, [String: String], Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.badServerResponse) }
            let (status, headers, data) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class GastroAPITests: XCTestCase {
    private static func bodyData(_ request: URLRequest) throws -> Data {
        if let body = request.httpBody { return body }
        let stream = try XCTUnwrap(request.httpBodyStream)
        stream.open(); defer { stream.close() }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count == 0 { return data }
            guard count > 0 else { throw stream.streamError ?? URLError(.cannotDecodeRawData) }
            data.append(contentsOf: buffer.prefix(count))
        }
    }
    private func client(token: String? = "sample-test-token") throws -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return try APIClient(baseURL: "https://example.test/api/v1", token: token, session: URLSession(configuration: config))
    }
    override func tearDown() { StubProtocol.handler = nil; super.tearDown() }

    func testRejectsURLWithEmbeddedCredentialsOrQuery() {
        for invalid in ["file:///tmp/key", "https://user:pass@example.test/api/v1", "https://example.test/api/v1?token=secret", "not a url"] {
            XCTAssertThrowsError(try APIClient(baseURL: invalid))
        }
    }

    func testUnknownNutritionStaysNullAndDecimalStringsPreserveFractions() throws {
        let data = Data("""
        {"calories_kcal":null,"protein_g":"12.75","fat_g":2.25,"carbs_g":null,
         "known_subtotals":{"calories_kcal":"120.5","protein_g":"12.75","fat_g":"2.25","carbs_g":0},
         "complete_fields":["protein_g","fat_g"],"sources":["unknown","estimated"]}
        """.utf8)
        let totals = try APIClient.decoder().decode(NutritionTotals.self, from: data)
        XCTAssertNil(totals.values.caloriesKcal)
        XCTAssertNil(totals.values.carbsG)
        XCTAssertEqual(totals.values.proteinG, Decimal(string: "12.75"))
        XCTAssertEqual(totals.knownSubtotals.caloriesKcal, Decimal(string: "120.5"))
        XCTAssertEqual(DisplayValues.number(totals.values.caloriesKcal), "нет данных")
    }

    func testBudgetUsesMinorUnitsWithoutFloatingPointRounding() {
        XCTAssertEqual(DisplayValues.minorUnits("900"), 90000)
        XCTAssertEqual(DisplayValues.minorUnits("750,50"), 75050)
        XCTAssertEqual(DisplayValues.minorUnits("0.01"), 1)
        XCTAssertNil(DisplayValues.minorUnits("1.005"))
        XCTAssertNil(DisplayValues.minorUnits("-10"))
        XCTAssertNil(DisplayValues.minorUnits("900abc"))
        XCTAssertNil(DisplayValues.decimalInput("NaN"))
        XCTAssertNil(DisplayValues.minorUnits("900000000000000000000"))
    }

    func testProfileExplicitNullClearsTargetWithoutSupplyingUserIdentity() throws {
        let patch = ProfilePatch(timezone: "Europe/Moscow", favoriteCuisines: [], dislikedIngredients: [],
                                 spicePreference: "any", allergies: [.init(code: "milk")],
                                 dailyCaloriesTarget: nil, defaultBudgetMinor: nil, proactiveEnabled: false)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: APIClient.encoder().encode(patch)) as? [String: Any])
        XCTAssertTrue(object["daily_calories_target"] is NSNull)
        XCTAssertTrue(object["default_budget_minor"] is NSNull)
        XCTAssertNil(object["user_id"])
    }

    func testLoginDoesNotSendPriorJWTAndUsesExactAuthPath() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/v1/auth/login")
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            return (200, [:], Data("""
            {"access_token":"new-token","token_type":"bearer","expires_in":3600,
             "user":{"id":"u","email":"test@example.test","role":"user"}}
            """.utf8))
        }
        let response = try await client().authenticate(email: "test@example.test", password: "test-password", register: false)
        XCTAssertEqual(response.accessToken, "new-token")
    }

    func testDiarySendsFractionAndStableIdempotencyWithoutUserId() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer sample-test-token")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Idempotency-Key"), "stable-operation-key")
            XCTAssertEqual(request.url?.path, "/api/v1/me/diary")
            let body = try Self.bodyData(request)
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual((object["portions"] as? NSNumber)?.decimalValue, Decimal(string: "0.5"))
            XCTAssertEqual(object["kind"] as? String, "menu_item")
            XCTAssertNil(object["user_id"])
            return (201, [:], Self.diaryData)
        }
        let entry = try await client().addDiary(.init(menuItemId: "m", portions: Decimal(string: "0.5")!), idempotencyKey: "stable-operation-key")
        XCTAssertEqual(entry.portions, Decimal(string: "0.5"))
        XCTAssertEqual(entry.consumedNutrition.values.caloriesKcal, Decimal(string: "310"))
    }

    func testRecommendation202AcceptsRetryAfterAndNoUserIdentity() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Idempotency-Key"), "recommendation-key")
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Self.bodyData(request)) as? [String: Any])
            XCTAssertNil(object["user_id"])
            let constraints = try XCTUnwrap(object["constraints"] as? [String: Any])
            XCTAssertEqual(constraints["budget_minor"] as? Int, 90000)
            return (202, ["Retry-After": "4"], Data("{\"id\":\"r\",\"status\":\"queued\",\"trigger\":\"manual\"}".utf8))
        }
        let response = try await client().createRecommendation(.init(query: "хинкали", constraints: .init(budgetMinor: 90000)), idempotencyKey: "recommendation-key")
        XCTAssertEqual(response.retryAfter, 4)
        XCTAssertFalse(response.run.isTerminal)
    }

    func testPollingStopsOnFailedRunAndPreservesServerError() async throws {
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.url?.path, "/api/v1/recommendations/r")
            return (200, [:], Data("""
            {"id":"r","status":"failed","trigger":"manual","error":{"code":"llm_unavailable","message":"Провайдер недоступен","request_id":"trace"}}
            """.utf8))
        }
        let run = try await client().waitForRecommendation(id: "r")
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(run.error?.code, "llm_unavailable")
        XCTAssertTrue(run.isTerminal)
    }

    func testUnauthorizedIsRecognizableByUI() async throws {
        StubProtocol.handler = { _ in
            (401, [:], Data("{\"code\":\"unauthorized\",\"message\":\"expired\",\"request_id\":\"q\"}".utf8))
        }
        do { _ = try await client().profile(); XCTFail("Expected 401") }
        catch let error as APIClientError { XCTAssertTrue(error.isUnauthorized) }
    }

    func testDeleteAccepts204WithoutDecodingEmptyBody() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.url?.path, "/api/v1/me/diary/d")
            return (204, [:], Data())
        }
        try await client().deleteDiary(id: "d")
    }

    private static let diaryData = Data("""
    {"id":"d","consumed_at":"2026-10-11T12:00:00Z","portions":"0.5","note":null,
     "snapshot":{"name":"Демо: хинкали","menu_item_id":"m","nutrition_per_portion":
       {"calories_kcal":620,"protein_g":32,"fat_g":22,"carbs_g":73.5,"source":"restaurant_declared","evidence_ids":["e"]}},
     "consumed_nutrition":{"calories_kcal":"310","protein_g":"16","fat_g":"11","carbs_g":"36.75",
       "known_subtotals":{"calories_kcal":310,"protein_g":16,"fat_g":11,"carbs_g":36.75},
       "complete_fields":["calories_kcal","protein_g","fat_g","carbs_g"],"sources":["restaurant_declared"]}}
    """.utf8)
}
