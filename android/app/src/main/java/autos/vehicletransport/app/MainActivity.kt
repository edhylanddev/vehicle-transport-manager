package autos.vehicletransport.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.print.PrintAttributes
import android.print.PrintManager
import android.provider.MediaStore
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.Toast
import android.window.OnBackInvokedDispatcher
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * A thin, careful wrapper around the vehicletransport.autos web app.
 *
 * Native pieces the web app relies on:
 *  - camera + gallery for <input type="file"> (inspection photos, receipts)
 *  - GPS for navigator.geolocation (pickup/delivery location stamps)
 *  - downloads that keep the login cookie (CSV exports)
 *  - printing / save-as-PDF for invoices and BOLs (window.VTMApp.print())
 *  - an update prompt driven by /app/version.json
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar

    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraUri: Uri? = null
    private var pendingGeo: Pair<String, GeolocationPermissions.Callback>? = null
    private var lastFailedUrl: String? = null

    private val baseHost: String by lazy { Uri.parse(BuildConfig.BASE_URL).host ?: "" }

    companion object {
        const val EXTRA_OPEN_PATH = "open_path"
        private const val REQ_NOTIFICATIONS = 13
        private const val REQ_FILE = 10
        private const val REQ_CAMERA_PERM = 11
        private const val REQ_LOCATION_PERM = 12
        private const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
    }

    // ------------------------------------------------------------------ lifecycle

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = FrameLayout(this)
        webView = WebView(this)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            isIndeterminate = false
            visibility = View.GONE
        }
        root.addView(webView, FrameLayout.LayoutParams(MATCH, MATCH))
        root.addView(progress, FrameLayout.LayoutParams(MATCH, dp(3)))
        setContentView(root)

        configureWebView()

        val openPath = pathFrom(intent)
        val restored = if (openPath == null) savedInstanceState?.let { webView.restoreState(it) } else null
        if (restored == null) {
            webView.loadUrl(BuildConfig.BASE_URL + (openPath ?: "/dashboard"))
        }

        registerBackHandler()
        checkForUpdate()
        Sunrise.sync(applicationContext) // re-arm the 6 AM alarm (e.g. after an app update)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pathFrom(intent)?.let { webView.loadUrl(BuildConfig.BASE_URL + it) }
    }

    /** Only same-site paths from our own notification, e.g. "/reports/sunrise". */
    private fun pathFrom(i: Intent?): String? {
        val p = i?.getStringExtra(EXTRA_OPEN_PATH) ?: return null
        return if (p.startsWith("/") && !p.startsWith("//")) p else null
    }

    private fun askNotificationPermissionIfNeeded() {
        if (Sunrise.shouldAskPermission(applicationContext)) {
            Sunrise.markAsked(applicationContext)
            requestPermissions(arrayOf(Sunrise.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onPause() {
        CookieManager.getInstance().flush()
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ web view

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setGeolocationEnabled(true)
            allowFileAccess = false
            allowContentAccess = true
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false) // target=_blank opens in the same view
            userAgentString = "$userAgentString VTMApp/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(webView, false)
        }
        webView.addJavascriptInterface(WebBridge(), "VTMApp")

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (isOurs(uri)) return false
                openExternally(uri) // phone numbers, Google Maps, other sites
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                CookieManager.getInstance().flush()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showOffline(request.url.toString())
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
                progress.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }

            override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                if (!isOurs(Uri.parse(origin))) {
                    callback.invoke(origin, false, false)
                    return
                }
                if (hasPerm(Manifest.permission.ACCESS_FINE_LOCATION) || hasPerm(Manifest.permission.ACCESS_COARSE_LOCATION)) {
                    callback.invoke(origin, true, false)
                } else {
                    pendingGeo = Pair(origin, callback)
                    requestPermissions(
                        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
                        REQ_LOCATION_PERM
                    )
                }
            }

            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: WebChromeClient.FileChooserParams
            ): Boolean {
                filePathCallback?.onReceiveValue(null) // cancel any chooser left open
                filePathCallback = callback
                if (params.isCaptureEnabled) {
                    if (hasPerm(Manifest.permission.CAMERA)) launchCamera()
                    else requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA_PERM)
                } else {
                    launchGallery(params.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE)
                }
                return true
            }
        }

        webView.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            download(url, userAgent, contentDisposition, mimeType)
        }
    }

    private fun isOurs(uri: Uri): Boolean {
        val host = uri.host ?: return false
        return uri.scheme == "https" && (host == baseHost || host == "www.$baseHost")
    }

    private fun openExternally(uri: Uri) {
        val intent = when (uri.scheme) {
            "tel" -> Intent(Intent.ACTION_DIAL, uri)
            "mailto", "sms", "smsto" -> Intent(Intent.ACTION_SENDTO, uri)
            else -> Intent(Intent.ACTION_VIEW, uri)
        }
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            toast("No app on this phone can open that link.")
        }
    }

    private fun showOffline(failedUrl: String) {
        lastFailedUrl = failedUrl
        val html = """
            <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>body{font-family:sans-serif;background:#f4f6f9;color:#1d2733;display:flex;min-height:90vh;
            align-items:center;justify-content:center;text-align:center;padding:24px}
            button{background:#0f3d6e;color:#fff;border:0;border-radius:12px;padding:14px 28px;font-size:17px}</style></head>
            <body><div><h2>No connection</h2>
            <p>Check your signal or Wi-Fi, then try again.<br>Anything you already saved is safe.</p>
            <button onclick="VTMApp.retry()">Try again</button></div></body></html>
        """.trimIndent()
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    // ------------------------------------------------------------------ photos

    private fun launchCamera() {
        try {
            val dir = File(cacheDir, "camera").apply { mkdirs() }
            dir.listFiles()?.filter { it.lastModified() < System.currentTimeMillis() - 86_400_000L }
                ?.forEach { it.delete() }
            val file = File.createTempFile("photo_", ".jpg", dir)
            val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
            cameraUri = uri
            val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                .putExtra(MediaStore.EXTRA_OUTPUT, uri)
                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            startActivityForResult(intent, REQ_FILE)
        } catch (e: Exception) {
            launchGallery(false) // no camera app: fall back to picking a file
        }
    }

    private fun launchGallery(multiple: Boolean) {
        cameraUri = null
        val pick = Intent(Intent.ACTION_GET_CONTENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "image/*"
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, multiple)
        }
        try {
            startActivityForResult(Intent.createChooser(pick, "Choose photos"), REQ_FILE)
        } catch (e: ActivityNotFoundException) {
            finishChooser(null)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_FILE) return
        var result: Array<Uri>? = null
        if (resultCode == RESULT_OK) {
            val clip = data?.clipData
            val single = data?.data
            val cam = cameraUri
            result = when {
                clip != null && clip.itemCount > 0 -> Array(clip.itemCount) { i -> clip.getItemAt(i).uri }
                single != null -> arrayOf(single)
                cam != null -> arrayOf(cam)
                else -> null
            }
        }
        finishChooser(result)
    }

    private fun finishChooser(result: Array<Uri>?) {
        filePathCallback?.onReceiveValue(result)
        filePathCallback = null
        cameraUri = null
    }

    // ------------------------------------------------------------------ permissions

    private fun hasPerm(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val granted = grantResults.any { it == PackageManager.PERMISSION_GRANTED }
        when (requestCode) {
            REQ_CAMERA_PERM -> {
                if (granted) {
                    launchCamera()
                } else {
                    toast("Camera not allowed — pick a photo from the gallery instead.")
                    launchGallery(true)
                }
            }
            REQ_LOCATION_PERM -> {
                pendingGeo?.let { (origin, cb) -> cb.invoke(origin, granted, false) }
                pendingGeo = null
            }
        }
    }

    // ------------------------------------------------------------------ downloads & printing

    private fun download(url: String, userAgent: String, contentDisposition: String?, mimeType: String?) {
        val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
        if (name.endsWith(".apk", ignoreCase = true)) {
            openExternally(Uri.parse(url)) // app updates install via the browser
            return
        }
        try {
            val req = DownloadManager.Request(Uri.parse(url)).apply {
                CookieManager.getInstance().getCookie(url)?.let { addRequestHeader("Cookie", it) }
                addRequestHeader("User-Agent", userAgent)
                if (mimeType != null) setMimeType(mimeType)
                setTitle(name)
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                } else {
                    setDestinationInExternalFilesDir(this@MainActivity, Environment.DIRECTORY_DOWNLOADS, name)
                }
            }
            (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
            toast("Downloading $name — check your notifications.")
        } catch (e: Exception) {
            toast("Download failed: ${e.message}")
        }
    }

    private fun printPage() {
        val pm = getSystemService(PRINT_SERVICE) as PrintManager
        val title = webView.title ?: getString(R.string.app_name)
        pm.print(title, webView.createPrintDocumentAdapter(title), PrintAttributes.Builder().build())
    }

    /** Exposed to the web pages as window.VTMApp */
    inner class WebBridge {
        @JavascriptInterface
        fun print() {
            runOnUiThread { printPage() }
        }

        @JavascriptInterface
        fun retry() {
            runOnUiThread { webView.loadUrl(lastFailedUrl ?: (BuildConfig.BASE_URL + "/dashboard")) }
        }

        @JavascriptInterface
        fun appVersion(): String = BuildConfig.VERSION_NAME

        /** Called by every page: is the signed-in user owner/office (gets the Sunrise report)? */
        @JavascriptInterface
        fun setSunrise(isStaff: Boolean) {
            Sunrise.setStaff(applicationContext, isStaff)
            if (isStaff) runOnUiThread { askNotificationPermissionIfNeeded() }
        }

        @JavascriptInterface
        fun getSunrisePref(): Boolean = Sunrise.isOptedIn(applicationContext)

        /** The on/off switch under My account. */
        @JavascriptInterface
        fun setSunrisePref(on: Boolean) {
            Sunrise.setOptedIn(applicationContext, on)
            if (on) runOnUiThread { askNotificationPermissionIfNeeded() }
        }
    }

    // ------------------------------------------------------------------ back button

    private fun registerBackHandler() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) {
                handleBack()
            }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        handleBack()
    }

    private fun handleBack() {
        if (webView.canGoBack()) webView.goBack() else finish()
    }

    // ------------------------------------------------------------------ updates

    private fun checkForUpdate() {
        thread(isDaemon = true) {
            try {
                val conn = URL(BuildConfig.BASE_URL + "/app/version.json").openConnection() as HttpURLConnection
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                conn.disconnect()
                val latest = json.optInt("versionCode", 0)
                val url = json.optString("url", "")
                if (latest > BuildConfig.VERSION_CODE && url.startsWith("https://")) {
                    runOnUiThread {
                        if (!isFinishing) showUpdateDialog(json.optString("versionName", ""), url)
                    }
                }
            } catch (e: Exception) {
                // offline or server unreachable: try again next launch
            }
        }
    }

    private fun showUpdateDialog(versionName: String, url: String) {
        AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("Version $versionName of the app is ready. Download and install it now?")
            .setPositiveButton("Update") { _, _ -> openExternally(Uri.parse(url)) }
            .setNegativeButton("Later", null)
            .show()
    }

    // ------------------------------------------------------------------ utils

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
