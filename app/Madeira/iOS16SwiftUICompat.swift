// iOS 16 / TrollStore port (BCD): iOS 17 SwiftUI API that BCD calls in many
// places, declared here with the same signatures and built on iOS 16 API.
// Declarations in this module shadow SwiftUI's own, so BCD's call sites stay
// exactly as upstream has them and a new BCD release merges without edits.

import SwiftUI

// MARK: - onChange(of:initial:_:) with the old and new value (iOS 17)

private struct BCDChangeObserver<V: Equatable>: ViewModifier {
    let value: V
    let initial: Bool
    let action: (V, V) -> Void
    @State private var last: V?

    func body(content: Content) -> some View {
        content
            .onAppear {
                if last == nil {
                    last = value
                    if initial { action(value, value) }
                }
            }
            .onChange(of: value, perform: { new in
                let old = last ?? new
                last = new
                action(old, new)
            })
    }
}

extension View {
    /// iOS 17's two-value onChange, on iOS 16's onChange(of:perform:).
    func onChange<V: Equatable>(of value: V, initial: Bool = false,
                                _ action: @escaping (_ oldValue: V, _ newValue: V) -> Void) -> some View {
        modifier(BCDChangeObserver(value: value, initial: initial, action: action))
    }

    /// iOS 17's onChange without arguments.
    func onChange<V: Equatable>(of value: V, initial: Bool = false,
                                _ action: @escaping () -> Void) -> some View {
        modifier(BCDChangeObserver(value: value, initial: initial, action: { _, _ in action() }))
    }
}

// MARK: - Distinct names: newer modifiers with a plain iOS 16 fallback

extension View {
    /// onScrollPhaseChange's "the person started scrolling" (iOS 18); a simultaneous drag on iOS 16 and 17.
    @ViewBuilder func bcdOnScrollInteracting(_ action: @escaping () -> Void) -> some View {
        if #available(iOS 18.0, *) {
            onScrollPhaseChange { _, phase in if phase == .interacting { action() } }
        } else {
            simultaneousGesture(DragGesture(minimumDistance: 8).onChanged { _ in action() })
        }
    }

    @ViewBuilder func bcdScrollClipDisabled() -> some View {
        if #available(iOS 17.0, *) { scrollClipDisabled() } else { self }
    }

    @ViewBuilder func bcdPresentationBackground(_ color: Color) -> some View {
        if #available(iOS 16.4, *) { presentationBackground(color) } else { self }
    }

    @ViewBuilder func bcdScrollBounceBasedOnSize() -> some View {
        if #available(iOS 16.4, *) { scrollBounceBehavior(.basedOnSize) } else { self }
    }

    /// searchable with isPresented (iOS 17); without it on iOS 16, where the field shows and hides itself.
    @ViewBuilder func bcdSearchable(text: Binding<String>, isPresented: Binding<Bool>, prompt: Text) -> some View {
        if #available(iOS 17.0, *) { searchable(text: text, isPresented: isPresented, prompt: prompt) }
        else { searchable(text: text, prompt: prompt) }
    }

    func bcdSearchable(text: Binding<String>, isPresented: Binding<Bool>, prompt: String) -> some View {
        bcdSearchable(text: text, isPresented: isPresented, prompt: Text(prompt))
    }
}

/// ScrollPosition (iOS 18) for the Duo interface's recent row; nothing on older iOS, where that interface never runs.
struct BCDScrollX {
    private var box: Any?
    init(x: CGFloat) {
        if #available(iOS 18.0, *) { box = ScrollPosition(x: x) }
    }
    mutating func scrollTo(x: CGFloat) {
        if #available(iOS 18.0, *), var p = box as? ScrollPosition { p.scrollTo(x: x); box = p }
    }
    @available(iOS 18.0, *)
    func binding(_ outer: Binding<BCDScrollX>) -> Binding<ScrollPosition> {
        Binding(get: { (outer.wrappedValue.box as? ScrollPosition) ?? ScrollPosition(x: 0) },
                set: { outer.wrappedValue.box = $0 })
    }
}

// MARK: - Settings rows with a hairline between them

/// BCDSettingsGroup's rows: Group(subviews:) on iOS 18; SwiftUI's variadic view (the same
/// child list, there since iOS 13) before it, so the separators look the same.
struct BCDSettingsRows<Content: View>: View {
    let content: Content
    var body: some View {
        if #available(iOS 18.0, *) {
            Group(subviews: content) { rows in
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        if index > 0 { BCDSettingsSeparator() }
                        row
                    }
                }
            }
        } else {
            _VariadicView.Tree(BCDSeparatedRowsLayout()) { content }
        }
    }
}

private struct BCDSeparatedRowsLayout: _VariadicView_MultiViewRoot {
    @ViewBuilder func body(children: _VariadicView.Children) -> some View {
        VStack(spacing: 0) {
            ForEach(children) { child in
                if child.id != children.first?.id { BCDSettingsSeparator() }
                child
            }
        }
    }
}
