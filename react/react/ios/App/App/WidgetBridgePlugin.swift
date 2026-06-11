import Foundation
import UIKit
import Capacitor
import WidgetKit

/// Capacitor 6 only auto-registers plugins that ship as npm packages (via
/// capacitor.config.json's packageClassList). App-local plugins like
/// WidgetBridgePlugin must be registered explicitly — done here through the
/// bridge's `capacitorDidLoad()` hook. Main.storyboard's root view controller is
/// repointed from CAPBridgeViewController to this subclass.
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(WidgetBridgePlugin())
    }
}

/// Bridges the web app's widget snapshot into the shared App Group container so
/// the NetWorthWidget extension can read it, then refreshes the widget.
///
/// JS side: `registerPlugin('WidgetBridge')` → `WidgetBridge.publish({ payload })`
/// (see `src/utils/widgetData.ts`). The App Group id must match the capability
/// added to BOTH the app and the widget targets in Xcode.
@objc(WidgetBridgePlugin)
public class WidgetBridgePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "WidgetBridgePlugin"
    public let jsName = "WidgetBridge"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "publish", returnType: CAPPluginReturnPromise)
    ]

    /// Must exactly match the App Group id configured on the app + widget targets.
    static let appGroupId = "group.com.mypokerface.app"
    static let snapshotKey = "widgetSnapshot"

    @objc func publish(_ call: CAPPluginCall) {
        guard let payload = call.getString("payload") else {
            call.reject("payload is required")
            return
        }
        guard let defaults = UserDefaults(suiteName: Self.appGroupId) else {
            call.reject("App Group \(Self.appGroupId) is not available")
            return
        }
        defaults.set(payload, forKey: Self.snapshotKey)
        if #available(iOS 14.0, *) {
            WidgetCenter.shared.reloadAllTimelines()
        }
        call.resolve()
    }
}
