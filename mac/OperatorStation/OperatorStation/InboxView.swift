import AVFoundation
import SwiftUI

@MainActor
final class InboxStore: ObservableObject {
    @Published var sms: [InboxSMS] = []
    @Published var voicemails: [InboxVoicemail] = []
    @Published var waiting = 0
    @Published var error: String?
    @Published var busy = false
    @Published var replyText = ""
    @Published var playingVMID: Int?

    private var player: AVPlayer?
    private weak var settings: StationSettings?

    func attach(settings: StationSettings) {
        self.settings = settings
    }

    func refresh() async {
        guard let settings else { return }
        busy = true
        error = nil
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            let payload = try await api.fetchInbox()
            sms = payload.sms
            voicemails = payload.voicemails
            waiting = payload.waiting
        } catch {
            self.error = error.localizedDescription
        }
    }

    func markHeardSMS(_ item: InboxSMS) async {
        await run { api in try await api.markSMSHeard(item.id) }
        await refresh()
    }

    func deleteSMS(_ item: InboxSMS) async {
        await run { api in try await api.deleteSMS(item.id) }
        await refresh()
    }

    func sendReply(to item: InboxSMS) async {
        let text = replyText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        await run { api in try await api.replySMS(id: item.id, text: text, confirm: true) }
        replyText = ""
        await refresh()
    }

    func markHeardVM(_ item: InboxVoicemail) async {
        await run { api in try await api.markVMHeard(item.id) }
        await refresh()
    }

    func deleteVM(_ item: InboxVoicemail) async {
        if playingVMID == item.id { stopPlayback() }
        await run { api in try await api.deleteVM(item.id) }
        await refresh()
    }

    func playVM(_ item: InboxVoicemail) async {
        guard let settings else { return }
        busy = true
        error = nil
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            let fileURL = try await api.downloadVMAudio(id: item.id, audioPath: item.audioURL)
            stopPlayback()
            let player = AVPlayer(url: fileURL)
            self.player = player
            playingVMID = item.id
            player.play()
            try? await api.markVMHeard(item.id)
            await refresh()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func stopPlayback() {
        player?.pause()
        player = nil
        playingVMID = nil
    }

    private func run(_ work: (ExchangeAPI) async throws -> Void) async {
        guard let settings else { return }
        busy = true
        error = nil
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            try await work(api)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct InboxView: View {
    @ObservedObject var settings: StationSettings
    @ObservedObject private var model = AppModel.shared
    @StateObject private var store = InboxStore()
    @State private var replyTarget: InboxSMS?
    @State private var highlightID: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Inbox")
                    .font(.title2.bold())
                if store.waiting > 0 {
                    Text("\(store.waiting) waiting")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Refresh") {
                    Task { await store.refresh() }
                }
                .disabled(store.busy)
            }
            .padding()

            if let error = store.error {
                Text(error)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }

            ScrollViewReader { proxy in
                List {
                    Section("Messages") {
                        if store.sms.isEmpty {
                            Text("No messages")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(store.sms) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(item.displayPeer).fontWeight(.semibold)
                                    Spacer()
                                    Text(Self.formatTime(item.createdAt))
                                        .foregroundStyle(.secondary)
                                        .font(.caption)
                                }
                                Text(item.body)
                                    .lineLimit(3)
                                HStack {
                                    if item.heardAt == nil && item.direction == "in" {
                                        Button("Heard") { Task { await store.markHeardSMS(item) } }
                                    }
                                    Button("Reply") { replyTarget = item }
                                    Button("Delete", role: .destructive) {
                                        Task { await store.deleteSMS(item) }
                                    }
                                }
                                .buttonStyle(.borderless)
                            }
                            .padding(.vertical, 2)
                            .padding(6)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(item.id == highlightID ? Color.accentColor.opacity(0.15) : Color.clear)
                            )
                            .id(item.id)
                        }
                    }

                    Section("Voicemail") {
                        if store.voicemails.isEmpty {
                            Text("No voicemail")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(store.voicemails) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(item.displayFrom).fontWeight(.semibold)
                                    Spacer()
                                    Text(Self.formatTime(item.createdAt))
                                        .foregroundStyle(.secondary)
                                        .font(.caption)
                                }
                                Text(String(format: "%.0fs", item.durationS))
                                    .foregroundStyle(.secondary)
                                    .font(.caption)
                                HStack {
                                    if store.playingVMID == item.id {
                                        Button("Stop") { store.stopPlayback() }
                                    } else {
                                        Button("Play") { Task { await store.playVM(item) } }
                                    }
                                    if item.heardAt == nil {
                                        Button("Heard") { Task { await store.markHeardVM(item) } }
                                    }
                                    Button("Delete", role: .destructive) {
                                        Task { await store.deleteVM(item) }
                                    }
                                }
                                .buttonStyle(.borderless)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
                .onChange(of: store.sms) { _, _ in
                    scrollToFocus(proxy: proxy)
                }
                .onChange(of: model.focusSMSID) { _, _ in
                    highlightID = model.focusSMSID
                    scrollToFocus(proxy: proxy)
                }
            }
        }
        .frame(minWidth: 480, minHeight: 420)
        .task {
            store.attach(settings: settings)
            highlightID = model.focusSMSID
            await store.refresh()
            // Refresh may complete before list lays out.
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        .sheet(item: $replyTarget) { item in
            VStack(alignment: .leading, spacing: 12) {
                Text("Reply to \(item.displayPeer)").font(.headline)
                TextField("Message", text: $store.replyText, axis: .vertical)
                    .lineLimit(3 ... 6)
                HStack {
                    Button("Cancel") { replyTarget = nil }
                    Spacer()
                    Button("Send") {
                        Task {
                            await store.sendReply(to: item)
                            replyTarget = nil
                        }
                    }
                    .disabled(store.replyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding()
            .frame(width: 360)
        }
    }

    private func scrollToFocus(proxy: ScrollViewProxy) {
        guard let id = model.focusSMSID ?? highlightID else { return }
        highlightID = id
        withAnimation {
            proxy.scrollTo(id, anchor: .center)
        }
    }

    private static func formatTime(_ ts: Double) -> String {
        let date = Date(timeIntervalSince1970: ts)
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}
