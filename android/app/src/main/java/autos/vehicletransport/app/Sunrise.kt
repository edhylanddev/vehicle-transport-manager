package autos.vehicletransport.app

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import java.util.Calendar

/**
 * The 6:00 AM "Sunrise report" notification.
 *
 * The web app tells us whether the signed-in user is owner/office staff
 * (window.VTMApp.setSunrise). Staff get a daily alarm unless they switch it
 * off under My account. Tapping the notification opens /reports/sunrise.
 */
object Sunrise {
    const val HOUR = 6
    const val MINUTE = 0
    const val PATH = "/reports/sunrise"
    const val CHANNEL_ID = "sunrise"
    const val ACTION_FIRE = "autos.vehicletransport.app.SUNRISE"
    private const val PREFS = "sunrise"
    private const val KEY_STAFF = "staff"
    private const val KEY_OPT_OUT = "opt_out"
    private const val KEY_ASKED = "asked_notification_permission"
    private const val REQUEST_CODE = 600
    const val POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun setStaff(ctx: Context, staff: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_STAFF, staff).apply()
        sync(ctx)
    }

    fun setOptedIn(ctx: Context, on: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_OPT_OUT, !on).apply()
        sync(ctx)
    }

    fun isOptedIn(ctx: Context): Boolean = !prefs(ctx).getBoolean(KEY_OPT_OUT, false)

    fun isWanted(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_STAFF, false) && isOptedIn(ctx)

    /** True the first time we should ask for notification permission (Android 13+). */
    fun shouldAskPermission(ctx: Context): Boolean {
        if (Build.VERSION.SDK_INT < 33 || !isWanted(ctx)) return false
        if (ctx.checkSelfPermission(POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return false
        return !prefs(ctx).getBoolean(KEY_ASKED, false)
    }

    fun markAsked(ctx: Context) {
        prefs(ctx).edit().putBoolean(KEY_ASKED, true).apply()
    }

    fun sync(ctx: Context) {
        if (isWanted(ctx)) schedule(ctx) else cancel(ctx)
    }

    private fun alarmIntent(ctx: Context): PendingIntent {
        val i = Intent(ctx, SunriseReceiver::class.java).setAction(ACTION_FIRE)
        return PendingIntent.getBroadcast(
            ctx, REQUEST_CODE, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /** Next 6:00 AM in the phone's own time zone. */
    fun nextTrigger(now: Long = System.currentTimeMillis()): Long {
        val c = Calendar.getInstance()
        c.timeInMillis = now
        c.set(Calendar.HOUR_OF_DAY, HOUR)
        c.set(Calendar.MINUTE, MINUTE)
        c.set(Calendar.SECOND, 0)
        c.set(Calendar.MILLISECOND, 0)
        if (c.timeInMillis <= now) c.add(Calendar.DAY_OF_YEAR, 1)
        return c.timeInMillis
    }

    fun schedule(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        // Inexact-but-Doze-friendly: usually fires within a few minutes of 6:00, no special permission needed.
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, nextTrigger(), alarmIntent(ctx))
    }

    fun cancel(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(alarmIntent(ctx))
    }

    private fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                val ch = NotificationChannel(CHANNEL_ID, "Sunrise report", NotificationManager.IMPORTANCE_DEFAULT)
                ch.description = "Yesterday's business, every morning at 6:00"
                nm.createNotificationChannel(ch)
            }
        }
    }

    fun showNotification(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannel(ctx)
        val open = Intent(ctx, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_OPEN_PATH, PATH)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val tap = PendingIntent.getActivity(
            ctx, REQUEST_CODE, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val builder: Notification.Builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(ctx, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(ctx)
        }
        val n = builder
            .setSmallIcon(R.drawable.ic_stat_sunrise)
            .setContentTitle("Your Sunrise report is ready")
            .setContentText("Yesterday's deliveries, payments and costs per mile.")
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(REQUEST_CODE, n)
    }
}

/** Fires the daily notification and re-arms the alarm after reboots, updates and clock changes. */
class SunriseReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Sunrise.ACTION_FIRE -> {
                if (Sunrise.isWanted(context)) {
                    Sunrise.showNotification(context)
                    Sunrise.schedule(context)
                }
            }
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            Intent.ACTION_TIME_CHANGED,
            Intent.ACTION_TIMEZONE_CHANGED -> Sunrise.sync(context)
        }
    }
}
