import SwiftUI

/// Edit open.meeting failover order on the Pi.
struct RoutingView: View {
    @ObservedObject var settings: StationSettings

    @State private var order: [String] = []
    @State private var stations: [String: RoutingStation] = [:]
    @State private var status = ""
    @State private var busy = false
    @State private var meetTarget = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Meet priority")
                .font(.headline)
            Text(
                "Digit 7 offers each station in order until one accepts. "
                    + "WE302 Meet is the handset SIP path."
            )
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            if !meetTarget.isEmpty {
                Text("Pi OPERATOR_MEET_JOIN_TARGET=\(meetTarget)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            List {
                ForEach(Array(order.enumerated()), id: \.element) { index, id in
                    HStack {
                        Text("\(index + 1).")
                            .foregroundStyle(.secondary)
                            .frame(width: 24, alignment: .trailing)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(stations[id]?.name ?? id)
                            Text(stationDetail(id))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button {
                            move(id, by: -1)
                        } label: {
                            Image(systemName: "arrow.up")
                        }
                        .disabled(index == 0 || busy)
                        .buttonStyle(.borderless)
                        Button {
                            move(id, by: 1)
                        } label: {
                            Image(systemName: "arrow.down")
                        }
                        .disabled(index >= order.count - 1 || busy)
                        .buttonStyle(.borderless)
                    }
                }
            }
            .listStyle(.inset)
            .frame(minHeight: 220)

            HStack {
                Button("Reload") { Task { await load() } }
                    .disabled(busy)
                Button("Save") { Task { await save() } }
                    .disabled(busy || order.isEmpty)
                Spacer()
                if !status.isEmpty {
                    Text(status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding()
        .frame(width: 520, height: 420)
        .task { await load() }
    }

    private func stationDetail(_ id: String) -> String {
        guard let station = stations[id] else { return id }
        let state = station.online ? "online" : "offline"
        return "\(id) · \(station.kind) · \(state)"
    }

    private func move(_ id: String, by delta: Int) {
        guard let idx = order.firstIndex(of: id) else { return }
        let next = idx + delta
        guard order.indices.contains(next) else { return }
        order.swapAt(idx, next)
        status = "Unsaved order"
    }

    private func load() async {
        busy = true
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            let payload = try await api.fetchRouting()
            var map: [String: RoutingStation] = [:]
            for station in payload.stations {
                map[station.clientId] = station
            }
            stations = map
            var seen = Set<String>()
            var next: [String] = []
            for id in payload.openMeetingOrder where seen.insert(id).inserted {
                next.append(id)
            }
            for station in payload.stations where seen.insert(station.clientId).inserted {
                // Keep known stations visible even if not yet in priority.
                if station.capabilities.contains("open_url")
                    || station.capabilities.contains("open.meeting")
                {
                    next.append(station.clientId)
                }
            }
            order = next
            meetTarget = payload.meetJoinTarget ?? ""
            status = "Loaded"
        } catch {
            status = error.localizedDescription
        }
    }

    private func save() async {
        busy = true
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            order = try await api.saveMeetingPriority(order)
            status = "Saved"
        } catch {
            status = error.localizedDescription
        }
    }
}
