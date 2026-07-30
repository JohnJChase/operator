import Foundation

enum ConnectionState: Equatable {
    case idle
    case connecting
    case connected
    case disconnected(String)

    var label: String {
        switch self {
        case .idle: return "Idle"
        case .connecting: return "Connecting…"
        case .connected: return "Connected"
        case .disconnected(let reason):
            return reason.isEmpty ? "Disconnected" : "Disconnected: \(reason)"
        }
    }

    var isOnline: Bool {
        if case .connected = self { return true }
        return false
    }
}

/// Ports `operator_os.mac_client` register + SSE + ack loop.
@MainActor
final class BridgeClient: ObservableObject {
    @Published private(set) var state: ConnectionState = .idle
    @Published private(set) var lastEvent: String = ""
    @Published private(set) var logLines: [String] = []

    private var runTask: Task<Void, Never>?
    private let shortSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    private let streamSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        // Long-lived SSE: do not idle-time-out between Pi keepalives.
        config.timeoutIntervalForRequest = 60 * 60 * 24
        config.timeoutIntervalForResource = 60 * 60 * 24 * 7
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    func start(settings: StationSettings) {
        stop()
        guard settings.isConfigured else {
            state = .disconnected("Set Pi URL and token in Settings")
            return
        }
        let base = settings.normalizedBaseURL
        let token = settings.token.trimmingCharacters(in: .whitespacesAndNewlines)
        let clientID = settings.clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = settings.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        state = .connecting
        appendLog("starting \(clientID) → \(base)")
        runTask = Task { [weak self] in
            await self?.runLoop(base: base, token: token, clientID: clientID, name: name)
        }
    }

    func stop() {
        runTask?.cancel()
        runTask = nil
        if state.isOnline || state == .connecting {
            state = .idle
        }
    }

    /// Ask the Pi to queue a notify to this client (proves SSE command path).
    func requestTestNotify(settings: StationSettings) async {
        let base = settings.normalizedBaseURL
        let token = settings.token.trimmingCharacters(in: .whitespacesAndNewlines)
        let clientID = settings.clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        appendLog("ping notify → Pi as \(clientID)")
        do {
            let url = try apiURL(base: base, path: "/api/desktop/notify")
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "title": "Operator Station",
                "body": "Test notify from Settings",
                "client_id": clientID,
            ])
            let (data, response) = try await shortSession.data(for: request)
            try throwIfNeeded(response: response, data: data)
            appendLog("ping accepted by Pi (wait for command…)")
        } catch {
            appendLog("ping failed: \(error.localizedDescription)")
        }
    }

    private func runLoop(base: String, token: String, clientID: String, name: String) async {
        while !Task.isCancelled {
            do {
                state = .connecting
                try await register(base: base, token: token, clientID: clientID, name: name)
                try await eventLoop(base: base, token: token, clientID: clientID)
            } catch is CancellationError {
                break
            } catch {
                let message = error.localizedDescription
                state = .disconnected(message)
                appendLog("disconnected: \(message)")
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    private func register(base: String, token: String, clientID: String, name: String) async throws {
        let url = try apiURL(base: base, path: "/api/desktop/register")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "client_id": clientID,
            "name": name,
            "capabilities": DesktopCommands.capabilities,
        ])
        let (data, response) = try await shortSession.data(for: request)
        try throwIfNeeded(response: response, data: data)
        appendLog("registered \(clientID) caps=\(DesktopCommands.capabilities.joined(separator: ","))")
    }

    private func eventLoop(base: String, token: String, clientID: String) async throws {
        var components = URLComponents(string: base + "/api/desktop/events")!
        components.queryItems = [URLQueryItem(name: "client_id", value: clientID)]
        guard let url = components.url else { throw BridgeError.badURL }
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")

        let (bytes, response) = try await streamSession.bytes(for: request)
        try throwIfNeeded(response: response, data: Data())
        state = .connected
        appendLog("listening")

        var buffer = Data()
        var lastKeepaliveLog = Date.distantPast
        for try await byte in bytes {
            try Task.checkCancellation()
            buffer.append(byte)
            let blocks = SSEFramer.pullBlocks(from: &buffer)
            for block in blocks {
                // Keepalive / comment-only blocks have no data field.
                if let text = String(data: block, encoding: .utf8),
                   text.split(whereSeparator: \.isNewline).allSatisfy({ $0.hasPrefix(":") || $0.isEmpty })
                {
                    let now = Date()
                    if now.timeIntervalSince(lastKeepaliveLog) >= 55 {
                        appendLog("keepalive")
                        lastKeepaliveLog = now
                    }
                    continue
                }
                guard let parsed = SSEFramer.parseBlock(block) else { continue }
                await handleSSE(
                    base: base,
                    token: token,
                    clientID: clientID,
                    event: parsed.event,
                    dataLines: parsed.data.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
                )
            }
        }
        throw BridgeError.streamEnded
    }

    private func handleSSE(
        base: String,
        token: String,
        clientID: String,
        event: String,
        dataLines: [String]
    ) async {
        guard !dataLines.isEmpty else { return }
        let eventName = event.trimmingCharacters(in: .whitespacesAndNewlines)
        let raw = dataLines.joined(separator: "\n")
        guard let data = raw.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            appendLog("bad sse json event=\(eventName)")
            return
        }

        if eventName == "ready" {
            lastEvent = "ready"
            appendLog("stream ready")
            return
        }
        if eventName != "command" {
            appendLog("sse event=\(eventName.isEmpty ? "(none)" : eventName)")
            return
        }

        let commandID: String = {
            if let s = payload["id"] as? String { return s }
            if let n = payload["id"] as? NSNumber { return n.stringValue }
            return ""
        }()
        let kind = payload["type"] as? String ?? "?"
        appendLog("command \(kind) id=\(commandID)")

        var status = "ok"
        var message = ""
        do {
            message = try await execute(payload)
            lastEvent = message
            appendLog(message)
        } catch {
            status = "error"
            message = error.localizedDescription
            appendLog("command failed: \(message)")
        }
        do {
            try await ack(
                base: base,
                token: token,
                clientID: clientID,
                commandID: commandID,
                status: status,
                message: message
            )
        } catch {
            appendLog("ack failed: \(error.localizedDescription)")
        }
    }

    private func execute(_ command: [String: Any]) async throws -> String {
        let kind = command["type"] as? String ?? ""
        let payload = command["payload"] as? [String: Any] ?? [:]
        switch kind {
        case "desktop.open_url":
            let raw = payload["url"] as? String ?? ""
            let url = try DesktopCommands.validateOpenURL(raw)
            DesktopCommands.openURL(url)
            return "opened \(url.absoluteString)"
        case "desktop.notify":
            let title = payload["title"] as? String ?? "Operator"
            let body = payload["body"] as? String ?? ""
            try await DesktopCommands.notify(title: title, body: body)
            return DesktopCommands.summary(title: title, body: body)
        default:
            throw CommandError.unsupported(kind)
        }
    }

    private func ack(
        base: String,
        token: String,
        clientID: String,
        commandID: String,
        status: String,
        message: String
    ) async throws {
        let url = try apiURL(base: base, path: "/api/desktop/ack")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "client_id": clientID,
            "command_id": commandID,
            "status": status,
            "message": message,
        ])
        let (data, response) = try await shortSession.data(for: request)
        try throwIfNeeded(response: response, data: data)
    }

    private func apiURL(base: String, path: String) throws -> URL {
        guard let url = URL(string: base + path) else { throw BridgeError.badURL }
        return url
    }

    private func throwIfNeeded(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200 ..< 300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let clipped = detail.count > 240 ? String(detail.prefix(240)) : detail
            throw BridgeError.http(http.statusCode, clipped)
        }
    }

    private func appendLog(_ line: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        logLines.append("\(stamp)  \(line)")
        if logLines.count > 80 {
            logLines.removeFirst(logLines.count - 80)
        }
    }
}

enum BridgeError: LocalizedError {
    case badURL
    case streamEnded
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "bad URL"
        case .streamEnded: return "SSE stream ended"
        case .http(let code, let detail):
            return detail.isEmpty ? "HTTP \(code)" : "HTTP \(code): \(detail)"
        }
    }
}
