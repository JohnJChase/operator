import Foundation

/// Minimal SSE framer. Does not use `AsyncBytes.lines` — blank event
/// terminators are easy to lose there, which yields keepalives with no
/// `ready`/`command` dispatch (exactly the Mac station failure mode).
enum SSEFramer {
    /// Pull complete event blocks (`…\n\n` or `…\r\n\r\n`) from a byte buffer.
    static func pullBlocks(from buffer: inout Data) -> [Data] {
        var blocks: [Data] = []
        while true {
            if let range = buffer.range(of: Data([0x0D, 0x0A, 0x0D, 0x0A])) {
                blocks.append(buffer.subdata(in: buffer.startIndex..<range.lowerBound))
                buffer.removeSubrange(buffer.startIndex..<range.upperBound)
                continue
            }
            if let range = buffer.range(of: Data([0x0A, 0x0A])) {
                blocks.append(buffer.subdata(in: buffer.startIndex..<range.lowerBound))
                buffer.removeSubrange(buffer.startIndex..<range.upperBound)
                continue
            }
            break
        }
        return blocks
    }

    /// Parse one SSE block into event name + data payload (data lines joined).
    static func parseBlock(_ block: Data) -> (event: String, data: String)? {
        guard let text = String(data: block, encoding: .utf8) else { return nil }
        var event = ""
        var dataLines: [String] = []
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        for raw in lines {
            var line = String(raw)
            if line.hasSuffix("\r") { line.removeLast() }
            if line.isEmpty { continue }
            if line.hasPrefix(":") { continue }
            if line.hasPrefix("event:") {
                event = String(line.dropFirst("event:".count))
                    .trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                var value = String(line.dropFirst("data:".count))
                if value.hasPrefix(" ") { value.removeFirst() }
                dataLines.append(value)
            }
        }
        guard !dataLines.isEmpty else { return nil }
        return (event, dataLines.joined(separator: "\n"))
    }
}
