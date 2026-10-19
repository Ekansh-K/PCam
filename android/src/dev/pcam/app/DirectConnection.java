package dev.pcam.app;

import android.Manifest;
import android.app.Activity;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.MacAddress;
import android.net.wifi.p2p.*;
import android.os.Build;
import java.util.function.Consumer;
import org.json.JSONObject;

/** Native Wi-Fi Direct client. It never changes the normal Wi-Fi connection. */
final class DirectConnection {
    private final Activity activity;
    private final WifiP2pManager manager;
    private final WifiP2pManager.Channel channel;
    private final Consumer<String> statusChanged, peerConnected;
    private volatile String status = "Shared Wi-Fi", owner = "", networkInterface = "";
    private boolean registered;
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION.equals(intent.getAction())) refresh();
        }
    };

    DirectConnection(Activity activity, Consumer<String> statusChanged, Consumer<String> peerConnected) {
        this.activity = activity; this.statusChanged = statusChanged; this.peerConnected = peerConnected;
        manager = (WifiP2pManager) activity.getSystemService(Context.WIFI_P2P_SERVICE);
        channel = manager == null ? null : manager.initialize(activity, activity.getMainLooper(),
                () -> update("Wi-Fi Direct service disconnected"));
        if (channel != null) {
            IntentFilter filter = new IntentFilter(WifiP2pManager.WIFI_P2P_CONNECTION_CHANGED_ACTION);
            if (Build.VERSION.SDK_INT >= 33) activity.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
            else activity.registerReceiver(receiver, filter);
            registered = true;
        }
    }

    private void update(String message) { status = message; statusChanged.accept(message); }
    boolean permitted() {
        String permission = Build.VERSION.SDK_INT >= 33 ? Manifest.permission.NEARBY_WIFI_DEVICES : Manifest.permission.ACCESS_FINE_LOCATION;
        if (activity.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) return true;
        activity.requestPermissions(new String[]{permission}, 2);
        update("Allow nearby devices, then try Wi-Fi Direct again.");
        return false;
    }
    void join(String ssid, String passphrase, String device, int frequency) {
        if (channel == null) { update("Wi-Fi Direct is unavailable"); return; }
        if (!permitted()) return;
        try {
            WifiP2pConfig.Builder builder = new WifiP2pConfig.Builder().setNetworkName(ssid)
                    .setPassphrase(passphrase).setGroupOperatingFrequency(frequency);
            if (device != null && !device.isEmpty()) builder.setDeviceAddress(MacAddress.fromString(device));
            update("Connecting directly to the laptop…");
            WifiP2pConfig config = builder.build();
            Runnable connect = () -> manager.connect(channel, config, new WifiP2pManager.ActionListener() {
                @Override public void onSuccess() { update("Wi-Fi Direct accepted. Waiting for an address…"); refresh(); }
                @Override public void onFailure(int reason) { update("Wi-Fi Direct connection failed: " + reason); }
            });
            manager.removeGroup(channel, new WifiP2pManager.ActionListener() {
                @Override public void onSuccess() { new android.os.Handler(activity.getMainLooper()).postDelayed(connect, 500); }
                @Override public void onFailure(int reason) { connect.run(); }
            });
        } catch (RuntimeException error) { update("Wi-Fi Direct: " + error.getMessage()); }
    }
    private void refresh() {
        if (channel == null || !permitted()) return;
        manager.requestConnectionInfo(channel, info -> {
            if (!info.groupFormed) { owner = ""; networkInterface = ""; return; }
            owner = info.groupOwnerAddress == null ? "" : info.groupOwnerAddress.getHostAddress();
            manager.requestGroupInfo(channel, group -> {
                if (group == null) return;
                networkInterface = group.getInterface();
                update(group.isGroupOwner() ? "Wi-Fi Direct ready for the laptop" : "Wi-Fi Direct connected to " + owner);
                if (!group.isGroupOwner() && !owner.isEmpty()) peerConnected.accept(owner);
            });
        });
    }
    void disconnect() {
        if (channel == null || !permitted()) return;
        manager.removeGroup(channel, new WifiP2pManager.ActionListener() {
            @Override public void onSuccess() { owner = ""; networkInterface = ""; update("Shared Wi-Fi"); }
            @Override public void onFailure(int reason) { update("Wi-Fi Direct disconnect failed: " + reason); }
        });
    }
    boolean connected() { return !owner.isEmpty(); }
    JSONObject details() throws org.json.JSONException {
        return new JSONObject().put("status", status).put("owner_address", owner).put("interface", networkInterface);
    }
    void close() { if (registered) activity.unregisterReceiver(receiver); if (channel != null) channel.close(); }
}
