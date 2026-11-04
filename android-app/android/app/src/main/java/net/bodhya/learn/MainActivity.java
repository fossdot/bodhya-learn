package net.bodhya.learn;

import com.getcapacitor.BridgeActivity;

/**
 * Reported on device 2026-08-21: the phone's back button CLOSED THE APP from any screen,
 * instead of stepping back one screen.
 *
 * The game is a single page. It now keeps exactly one spare history entry armed whenever the
 * current screen has a back handler (see armBack() and the popstate listener in index.html).
 * That alone was not enough: Capacitor does not consult the WebView's back-forward list by
 * itself unless the @capacitor/app plugin is installed and a backButton listener is
 * registered. With no such listener, Android went straight to finishing the activity and the
 * page never heard the press — verified on the emulator, where back moved focus from
 * MainActivity to the launcher even though the JS trap was armed.
 *
 * Rather than add a plugin for one behaviour, ask the WebView directly. Going back in the
 * WebView fires popstate, which runs the very same handler the on-screen back button runs, so
 * the two routes cannot drift apart. At home nothing is armed, canGoBack() is false, and the
 * default (leave the app) is exactly what a launcher-level back should do.
 *
 * onBackPressed() is deprecated from API 33 but is still dispatched here, because the manifest
 * does not set android:enableOnBackInvokedCallback="true". If that flag is ever turned on,
 * this logic must move to an OnBackPressedCallback registered on the back-pressed dispatcher,
 * or the app will start closing from every screen again.
 */
public class MainActivity extends BridgeActivity {
    @Override
    public void onBackPressed() {
        if (getBridge() != null
                && getBridge().getWebView() != null
                && getBridge().getWebView().canGoBack()) {
            getBridge().getWebView().goBack();
        } else {
            super.onBackPressed();
        }
    }
}
