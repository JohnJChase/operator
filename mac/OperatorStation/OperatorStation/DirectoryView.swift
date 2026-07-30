import SwiftUI

@MainActor
final class DirectoryStore: ObservableObject {
    @Published var contacts: [ExchangeContact] = []
    @Published var error: String?
    @Published var busy = false
    @Published var draftName = ""
    @Published var draftE164 = ""
    @Published var draftShortCode = ""
    @Published var statusMessage: String?

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
            contacts = try await api.fetchContacts()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func saveDraft(editing: ExchangeContact? = nil) async {
        let name = draftName.trimmingCharacters(in: .whitespacesAndNewlines)
        let e164 = draftE164.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !e164.isEmpty else {
            error = "Name and number required"
            return
        }
        await run { api in
            _ = try await api.upsertContact(
                id: editing?.id,
                name: name,
                e164: e164,
                shortCode: draftShortCode.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
        clearDraft()
        statusMessage = "Saved \(name)"
        await refresh()
    }

    func delete(_ contact: ExchangeContact) async {
        await run { api in try await api.deleteContact(id: contact.id) }
        await refresh()
    }

    func importFromMacContacts() async {
        guard let picked = await MacContactPicker.pickPhoneNumber() else {
            error = "No contact selected (check Contacts permission in System Settings)"
            return
        }
        draftName = picked.name
        draftE164 = picked.e164
        statusMessage = "Imported \(picked.name) — set a short code if you want quick dial, then Save"
    }

    func loadForEdit(_ contact: ExchangeContact) {
        draftName = contact.name
        draftE164 = contact.e164
        draftShortCode = contact.shortCode
    }

    func clearDraft() {
        draftName = ""
        draftE164 = ""
        draftShortCode = ""
    }

    private func run(_ work: (ExchangeAPI) async throws -> Void) async {
        guard let settings else { return }
        busy = true
        error = nil
        defer { busy = false }
        do {
            try await work(try ExchangeAPI.from(settings: settings))
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct DirectoryView: View {
    @ObservedObject var settings: StationSettings
    @StateObject private var store = DirectoryStore()
    @State private var editing: ExchangeContact?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Directory")
                    .font(.title2.bold())
                Spacer()
                Button("Import from Mac Contacts…") {
                    Task { await store.importFromMacContacts() }
                }
                Button("Refresh") { Task { await store.refresh() } }
            }
            .padding()

            if let error = store.error {
                Text(error).foregroundStyle(.red).padding(.horizontal)
            }
            if let status = store.statusMessage {
                Text(status).foregroundStyle(.secondary).padding(.horizontal)
            }

            GroupBox("Contact") {
                Form {
                    TextField("Name", text: $store.draftName)
                    TextField("Number", text: $store.draftE164)
                    TextField("Short code (quick dial)", text: $store.draftShortCode)
                    HStack {
                        Button(editing == nil ? "Add to exchange" : "Save") {
                            Task {
                                await store.saveDraft(editing: editing)
                                editing = nil
                            }
                        }
                        .disabled(store.busy)
                        if editing != nil {
                            Button("Cancel edit") {
                                editing = nil
                                store.clearDraft()
                            }
                        }
                    }
                }
                .padding(4)
            }
            .padding(.horizontal)

            List {
                if store.contacts.isEmpty {
                    Text("No exchange contacts yet")
                        .foregroundStyle(.secondary)
                }
                ForEach(store.contacts) { contact in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(contact.name).fontWeight(.semibold)
                            Text(contact.e164).foregroundStyle(.secondary)
                            if !contact.shortCode.isEmpty {
                                Text("*\(contact.shortCode)").font(.caption)
                            }
                        }
                        Spacer()
                        Button("Edit") {
                            editing = contact
                            store.loadForEdit(contact)
                        }
                        Button("Delete", role: .destructive) {
                            Task { await store.delete(contact) }
                        }
                    }
                }
            }
        }
        .frame(minWidth: 480, minHeight: 420)
        .task {
            store.attach(settings: settings)
            await store.refresh()
        }
    }
}
