import Foundation
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

    @Test func sseFramerParsesReadyAndCommandBlocks() {
        var buffer = Data()
        buffer.append(contentsOf: """
            event: ready
            data: {"ok":true}

            event: command
            data: {"id":"abc","type":"desktop.notify","payload":{"title":"Operator","body":"hi"}}

            : keepalive

            """.data(using: .utf8)!)

        let blocks = SSEFramer.pullBlocks(from: &buffer)
        #expect(blocks.count == 3)

        let ready = SSEFramer.parseBlock(blocks[0])
        #expect(ready?.event == "ready")
        #expect(ready?.data.contains("\"ok\":true") == true)

        let command = SSEFramer.parseBlock(blocks[1])
        #expect(command?.event == "command")
        #expect(command?.data.contains("desktop.notify") == true)

        #expect(SSEFramer.parseBlock(blocks[2]) == nil)
        #expect(buffer.isEmpty)
    }

    @Test func sseFramerHandlesCRLF() {
        var buffer = Data("event: ready\r\ndata: {\"ok\":true}\r\n\r\n".utf8)
        let blocks = SSEFramer.pullBlocks(from: &buffer)
        #expect(blocks.count == 1)
        #expect(SSEFramer.parseBlock(blocks[0])?.event == "ready")
    }
}
