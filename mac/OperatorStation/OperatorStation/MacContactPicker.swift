import Contacts
import SwiftUI

struct MacContactPick: Equatable {
    var name: String
    var e164: String
    var label: String
}

/// In-app Contacts browser. Avoids ``CNContactPicker``, which often won't let a
/// menu-bar app select a phone number row.
struct MacContactsPickView: View {
    var onPick: (MacContactPick) -> Void
    var onCancel: () -> Void

    @State private var rows: [Row] = []
    @State private var filter = ""
    @State private var error: String?
    @State private var loading = true

    private struct Row: Identifiable {
        var id: String
        var name: String
        var label: String
        var e164: String
    }

    private var filtered: [Row] {
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return rows }
        return rows.filter {
            $0.name.lowercased().contains(q) || $0.e164.contains(q) || $0.label.lowercased().contains(q)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Mac Contacts").font(.headline)
                Spacer()
                Button("Cancel", action: onCancel)
            }
            .padding()

            TextField("Search", text: $filter)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal)

            if loading {
                ProgressView("Loading contacts…")
                    .padding()
            } else if let error {
                Text(error)
                    .foregroundStyle(.red)
                    .padding()
            } else if filtered.isEmpty {
                Text("No phone numbers found")
                    .foregroundStyle(.secondary)
                    .padding()
            } else {
                List(filtered) { row in
                    Button {
                        onPick(MacContactPick(name: row.name, e164: row.e164, label: row.label))
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.name).fontWeight(.semibold)
                            Text("\(row.label): \(row.e164)")
                                .foregroundStyle(.secondary)
                                .font(.caption)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .frame(width: 420, height: 480)
        .task { await load() }
    }

    private func load() async {
        loading = true
        error = nil
        defer { loading = false }

        let store = CNContactStore()
        let status = CNContactStore.authorizationStatus(for: .contacts)
        if status == .notDetermined {
            do {
                let ok = try await store.requestAccess(for: .contacts)
                guard ok else {
                    error = "Contacts access denied"
                    return
                }
            } catch {
                self.error = error.localizedDescription
                return
            }
        } else if status != .authorized {
            error = "Allow Contacts for Operator Station in System Settings → Privacy & Security → Contacts"
            return
        }

        let keys: [CNKeyDescriptor] = [
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactOrganizationNameKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        request.sortOrder = .userDefault

        var built: [Row] = []
        do {
            try store.enumerateContacts(with: request) { contact, _ in
                let name = displayName(contact)
                for phone in contact.phoneNumbers {
                    let raw = phone.value.stringValue
                    let digits = raw.filter { $0.isNumber || $0 == "+" }
                    guard !digits.isEmpty else { continue }
                    let label = CNLabeledValue<NSString>.localizedString(
                        forLabel: phone.label ?? CNLabelPhoneNumberMobile
                    )
                    built.append(
                        Row(
                            id: "\(contact.identifier)-\(digits)-\(label)",
                            name: name,
                            label: label,
                            e164: digits
                        )
                    )
                }
            }
            rows = built
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func displayName(_ contact: CNContact) -> String {
        let person = "\(contact.givenName) \(contact.familyName)"
            .trimmingCharacters(in: .whitespaces)
        if !person.isEmpty { return person }
        let org = contact.organizationName.trimmingCharacters(in: .whitespaces)
        return org.isEmpty ? "Contact" : org
    }
}

enum MacContactPicker {
    @MainActor
    static func pickPhoneNumber() async -> MacContactPick? {
        // Prefer in-app sheet hosts (Directory / Place Call). Fallback window
        // for any caller that isn't already in a sheet context.
        await withCheckedContinuation { (continuation: CheckedContinuation<MacContactPick?, Never>) in
            let hosting = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 420, height: 480),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false
            )
            hosting.title = "Mac Contacts"
            hosting.isReleasedWhenClosed = false
            final class Box {
                var resumed = false
                var window: NSWindow?
            }
            let box = Box()
            box.window = hosting

            func finish(_ value: MacContactPick?) {
                guard !box.resumed else { return }
                box.resumed = true
                box.window?.close()
                box.window = nil
                continuation.resume(returning: value)
            }

            let root = MacContactsPickView(
                onPick: { finish($0) },
                onCancel: { finish(nil) }
            )
            hosting.contentViewController = NSHostingController(rootView: root)
            hosting.center()
            hosting.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}
