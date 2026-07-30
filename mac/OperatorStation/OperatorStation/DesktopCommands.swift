import AppKit
import Foundation
import UserNotifications

enum DesktopCommands {
    static let capabilities = ["open_url", "notify"]

    static func validateOpenURL(_ raw: String) throws -> URL {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host != nil
        else {
            throw CommandError.invalidURL
        }
        if url.user != nil || url.password != nil {
            throw CommandError.urlHasCredentials
        }
        return url
    }

    static func openURL(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    static func requestNotificationPermission() async {
        let center = UNUserNotificationCenter.current()
        _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
    }

    static func notify(title: String, body: String) async throws {
        let center = UNUserNotificationCenter.current()
        let content = UNMutableNotificationContent()
        content.title = String(title.prefix(80)).isEmpty ? "Operator" : String(title.prefix(80))
        content.body = String(body.prefix(240))
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        try await center.add(request)
    }

    static func summary(title: String, body: String) -> String {
        let t = title.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let clippedTitle = String(t.prefix(80)).isEmpty ? "Operator" : String(t.prefix(80))
        var b = body.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        if b.count > 180 {
            b = String(b.prefix(177)).trimmingCharacters(in: .whitespaces) + "..."
        }
        return b.isEmpty ? clippedTitle : "\(clippedTitle): \(b)"
    }
}

enum CommandError: LocalizedError, Equatable {
    case invalidURL
    case urlHasCredentials
    case unsupported(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "url must be http(s)"
        case .urlHasCredentials: return "url must not include credentials"
        case .unsupported(let kind): return "unsupported command: \(kind)"
        }
    }
}
