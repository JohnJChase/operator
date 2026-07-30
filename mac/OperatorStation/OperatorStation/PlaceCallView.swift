import SwiftUI

struct PlaceCallView: View {
    @ObservedObject var settings: StationSettings
    @State private var contacts: [ExchangeContact] = []
    @State private var number = ""
    @State private var selectedID: Int?
    @State private var message: String?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Place call")
                .font(.title2.bold())
            Text("The WE302 will ring. Pick up to place the SIP call.")
                .foregroundStyle(.secondary)

            if let error {
                Text(error).foregroundStyle(.red)
            }
            if let message {
                Text(message).foregroundStyle(.green)
            }

            GroupBox("Exchange contact") {
                if contacts.isEmpty {
                    Text("No exchange contacts — import some in Directory, or dial a number below.")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Contact", selection: $selectedID) {
                        Text("Select…").tag(Optional<Int>.none)
                        ForEach(contacts) { c in
                            Text("\(c.name) (\(c.e164))").tag(Optional(c.id))
                        }
                    }
                    Button("Call selected") {
                        Task { await callSelectedExchange() }
                    }
                    .disabled(selectedID == nil || busy)
                }
            }

            GroupBox("Number") {
                HStack {
                    TextField("E.164 or local number", text: $number)
                        .textFieldStyle(.roundedBorder)
                    Button("From Mac Contacts…") {
                        Task { await pickFromMacContacts() }
                    }
                }
                Button("Call this number") {
                    Task { await callNumber(number) }
                }
                .disabled(number.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
                .keyboardShortcut(.defaultAction)
            }

            Spacer()
        }
        .padding()
        .frame(minWidth: 420, minHeight: 320)
        .task { await refreshContacts() }
    }

    private func refreshContacts() async {
        do {
            let api = try ExchangeAPI.from(settings: settings)
            contacts = try await api.fetchContacts()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func pickFromMacContacts() async {
        error = nil
        guard let picked = await MacContactPicker.pickPhoneNumber() else {
            error = "No contact selected (check Contacts permission)"
            return
        }
        number = picked.e164
        message = "Using \(picked.name)"
    }

    private func callSelectedExchange() async {
        guard let id = selectedID, let contact = contacts.first(where: { $0.id == id }) else { return }
        await callNumber(contact.e164)
    }

    private func callNumber(_ raw: String) async {
        let e164 = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !e164.isEmpty else { return }
        busy = true
        error = nil
        message = nil
        defer { busy = false }
        do {
            let api = try ExchangeAPI.from(settings: settings)
            let dest = try await api.placeCall(e164: e164)
            message = "Requested \(dest) — pick up the WE302 when it rings"
        } catch {
            self.error = error.localizedDescription
        }
    }
}
