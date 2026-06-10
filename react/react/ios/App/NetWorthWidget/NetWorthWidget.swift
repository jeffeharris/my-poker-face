import WidgetKit
import SwiftUI

// Must match WidgetBridgePlugin.appGroupId / snapshotKey and the App Group
// capability added to both the app and this widget target in Xcode.
private let appGroupId = "group.com.mypokerface.app"
private let snapshotKey = "widgetSnapshot"

/// Mirrors the JSON written by src/utils/widgetData.ts → WidgetBridge.publish.
struct WidgetSnapshot: Codable {
    let netWorth: Double
    let series: [Double]
    let renown: Double
    let regard: Double
    let status: String
    let updatedAt: String
}

struct NetWorthEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetSnapshot?

    static let empty = NetWorthEntry(date: Date(), snapshot: nil)
    static let sample = NetWorthEntry(
        date: Date(),
        snapshot: WidgetSnapshot(
            netWorth: 12_500, series: [8_000, 9_000, 8_400, 11_200, 10_600, 12_500],
            renown: 0.62, regard: 0.35, status: "Rising Figure", updatedAt: ""
        )
    )
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> NetWorthEntry { .sample }

    func getSnapshot(in context: Context, completion: @escaping (NetWorthEntry) -> Void) {
        completion(context.isPreview ? .sample : loadEntry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NetWorthEntry>) -> Void) {
        // The app pushes a reload on each lobby refresh; this is just a fallback
        // cadence so the "updated" feel stays fresh if the app isn't opened.
        let next = Calendar.current.date(byAdding: .minute, value: 30, to: Date())
            ?? Date().addingTimeInterval(1800)
        completion(Timeline(entries: [loadEntry()], policy: .after(next)))
    }

    private func loadEntry() -> NetWorthEntry {
        guard
            let raw = UserDefaults(suiteName: appGroupId)?.string(forKey: snapshotKey),
            let data = raw.data(using: .utf8),
            let snap = try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
        else {
            return .empty
        }
        return NetWorthEntry(date: Date(), snapshot: snap)
    }
}

/// Minimal line sparkline; green when up over the window, red when down.
struct Sparkline: View {
    let values: [Double]

    var body: some View {
        GeometryReader { geo in
            if values.count >= 2 {
                let minV = values.min() ?? 0
                let maxV = values.max() ?? 1
                let range = max(maxV - minV, 1)
                let stepX = geo.size.width / CGFloat(values.count - 1)
                Path { path in
                    for (i, v) in values.enumerated() {
                        let x = CGFloat(i) * stepX
                        let y = geo.size.height * (1 - CGFloat((v - minV) / range))
                        if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                        else { path.addLine(to: CGPoint(x: x, y: y)) }
                    }
                }
                .stroke(trendColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }
        }
    }

    private var trendColor: Color {
        guard let first = values.first, let last = values.last else { return .secondary }
        return last >= first ? .green : .red
    }
}

struct NetWorthWidgetEntryView: View {
    var entry: Provider.Entry

    var body: some View {
        if let s = entry.snapshot {
            VStack(alignment: .leading, spacing: 6) {
                Text("NET WORTH")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                Text(currency(s.netWorth))
                    .font(.title3.weight(.bold))
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)
                Sparkline(values: s.series).frame(maxWidth: .infinity).frame(height: 26)
                Text(s.status.isEmpty ? "Unranked" : s.status)
                    .font(.system(size: 11, weight: .semibold))
                    .lineLimit(1)
                HStack(spacing: 12) {
                    Label("\(Int((s.renown * 100).rounded()))", systemImage: "star.fill")
                    Label(regardText(s.regard), systemImage: "heart.fill")
                }
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("My Poker Face").font(.caption.bold())
                Text("Open the app to sync your stats.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func currency(_ v: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "$\(Int(v))"
    }

    private func regardText(_ r: Double) -> String {
        let pct = Int((r * 100).rounded())
        return pct > 0 ? "+\(pct)" : "\(pct)"
    }
}

struct NetWorthWidget: Widget {
    let kind = "NetWorthWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: Provider()) { entry in
            if #available(iOS 17.0, *) {
                NetWorthWidgetEntryView(entry: entry)
                    .containerBackground(.fill.tertiary, for: .widget)
            } else {
                NetWorthWidgetEntryView(entry: entry)
                    .padding()
            }
        }
        .configurationDisplayName("Net Worth")
        .description("Your net worth trend, renown, and standing.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
