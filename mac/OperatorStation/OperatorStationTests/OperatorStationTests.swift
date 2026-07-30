import Testing
@testable import OperatorStation

struct OperatorStationTests {
    @Test func validateOpenURLAcceptsHTTPS() throws {
        let url = try DesktopCommands.validateOpenURL("https://meet.google.com/abc-defg-hij")
        #expect(url.host == "meet.google.com")
    }

    @Test func validateOpenURLRejectsCredentials() {
        #expect(throws: CommandError.urlHasCredentials) {
            try DesktopCommands.validateOpenURL("https://user:pass@example.com/x")
        }
    }

    @Test func validateOpenURLRejectsNonHTTP() {
        #expect(throws: CommandError.invalidURL) {
            try DesktopCommands.validateOpenURL("file:///tmp/x")
        }
    }

    @Test func notificationSummaryClipsBody() {
        let body = String(repeating: "a", count: 200)
        let summary = DesktopCommands.summary(title: "Operator", body: body)
        #expect(summary.hasPrefix("Operator: "))
        #expect(summary.hasSuffix("..."))
        #expect(summary.count <= 12 + 180)
    }
}
