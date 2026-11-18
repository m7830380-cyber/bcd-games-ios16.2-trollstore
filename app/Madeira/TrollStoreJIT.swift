// iOS 16 / TrollStore port (BCD): JIT through TrollStore instead of StikDebug or
// Built-in StikJIT, which need iOS 17.4 and iOS 26.
//
// TrollStore's "Open with JIT" (long-press BCD in TrollStore's app list) and its
// apple-magnifier://enable-jit URL (TrollStore 2.0.9 and later) have its root
// helper attach to BCD with ptrace and detach at once. That leaves CS_DEBUGGED
// set and no debugger attached, the state JITNoDebuggerPool already handles:
// before iOS 26 there is no TXM, so CS_DEBUGGED alone lets BCD map its own
// executable pool (JITAllocator.c, "JIT MEMORY WITHOUT A DEBUGGER"), and
// nothing has to answer the BRK #0xf00d pool request.
//
// env.MADEIRA_JIT_TROLLSTORE = 0/1 forces the choice either way.

import UIKit

enum TrollStoreJIT {
    static let url = "apple-magnifier://enable-jit"

    /// TrollStore exists up to iOS 17.0; StikDebug starts at 17.4. Below 17 it is
    /// the only way to JIT; on 17.0 it is taken when TrollStore answers.
    static var preferred: Bool {
        if let e = getenv("MADEIRA_JIT_TROLLSTORE"), e.pointee == 0x30 || e.pointee == 0x31 {
            return e.pointee == 0x31
        }
        let major = ProcessInfo.processInfo.operatingSystemVersion
        if major.majorVersion < 17 { return true }
        if major.majorVersion == 17 && major.minorVersion < 4 { return installed }
        return false
    }

    /// TrollStore answers its URL scheme (needs apple-magnifier in LSApplicationQueriesSchemes).
    static var installed: Bool {
        guard let u = URL(string: "apple-magnifier://") else { return false }
        return UIApplication.shared.canOpenURL(u)
    }

    static let openWithJITAdvice = "Close BCD (swipe it away in the app switcher), then open it from TrollStore: "
        + "long-press BCD in TrollStore's app list and choose \"Open with JIT\"."

    /// Asks TrollStore to enable JIT for this running app, then waits for CS_DEBUGGED.
    static func enable(timeout: TimeInterval = 30, completion: @escaping (Result<Void, Error>) -> Void) {
        let failed = { (message: String) in
            LogStore.shared.log("[jit-trollstore] " + message, level: .error)
            completion(.failure(NSError(domain: "MadeiraJIT", code: 21,
                                        userInfo: [NSLocalizedDescriptionKey: message])))
        }
        if SigningStatus.current.debugged {
            LogStore.shared.log("[jit-trollstore] CS_DEBUGGED is already set", level: .success)
            completion(.success(()))
            return
        }
        guard let bundleID = Bundle.main.bundleIdentifier,
              let request = URL(string: "\(url)?bundle-id=\(bundleID)") else {
            failed("BCD could not make the TrollStore request.")
            return
        }
        LogStore.shared.log("[jit-trollstore] asking TrollStore to enable JIT for \(bundleID)")
        UIApplication.shared.open(request, options: [:]) { opened in
            guard opened else {
                failed("TrollStore did not answer. " + openWithJITAdvice)
                return
            }
            let deadline = Date().addingTimeInterval(timeout)
            let timer = Timer(timeInterval: 0.5, repeats: true) { timer in
                if SigningStatus.current.debugged {
                    timer.invalidate()
                    LogStore.shared.log("[jit-trollstore] JIT enabled by TrollStore (CS_DEBUGGED set)", level: .success)
                    completion(.success(()))
                } else if Date() >= deadline {
                    timer.invalidate()
                    failed("TrollStore did not enable JIT. Update TrollStore to 2.0.9 or later, or: " + openWithJITAdvice)
                }
            }
            RunLoop.main.add(timer, forMode: .common)
        }
    }
}
