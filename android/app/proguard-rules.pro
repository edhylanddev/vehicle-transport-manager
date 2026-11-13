# Keep the JavaScript bridge methods the web pages call (window.VTMApp.*)
-keepclassmembers class autos.vehicletransport.app.MainActivity$WebBridge {
    @android.webkit.JavascriptInterface <methods>;
}
