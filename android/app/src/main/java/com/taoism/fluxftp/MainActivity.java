package com.taoism.fluxftp;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Adaptive companion UI for FluxFTP's authenticated loopback API. */
public final class MainActivity extends Activity {
    private static final int BLUE = Color.rgb(23, 105, 170);
    private static final int INK = Color.rgb(27, 39, 53);
    private static final int MUTED = Color.rgb(96, 109, 124);
    private static final int CARD = Color.WHITE;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<Transfer> transfers = new ArrayList<>();
    private final List<String> sites = new ArrayList<>();
    private ApiClient api;
    private String page = "Queue";
    private LinearLayout content;
    private TextView connectionLabel;
    private TextView messageLabel;
    private EditText urlField;
    private EditText tokenField;
    private Spinner sourceSite;
    private Spinner destinationSite;
    private EditText sourcePath;
    private EditText destinationPath;
    private CheckBox directoryCheck;
    private CheckBox startCheck;
    private boolean busy;
    private boolean connected;

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (api != null && page.equals("Queue") && !isFinishing()) refresh(false);
            handler.postDelayed(this, 8000);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(245, 247, 250));
        getWindow().setNavigationBarColor(Color.rgb(245, 247, 250));
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        String savedUrl = getPreferences(MODE_PRIVATE).getString("api_url", "http://127.0.0.1:8765");
        String savedToken = new TokenVault(this).read();
        api = savedToken.isEmpty() ? null : new ApiClient(savedUrl, savedToken);
        render();
        if (api != null) refresh(true);
    }

    @Override protected void onResume() { super.onResume(); handler.postDelayed(poll, 8000); }
    @Override protected void onPause() { handler.removeCallbacks(poll); super.onPause(); }
    @Override protected void onDestroy() { worker.shutdownNow(); super.onDestroy(); }

    private int dp(float value) { return (int) (value * getResources().getDisplayMetrics().density + 0.5f); }

    private LinearLayout column() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        return view;
    }

    private LinearLayout row() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.HORIZONTAL);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    private LinearLayout.LayoutParams lp(int width, int height) { return new LinearLayout.LayoutParams(width, height); }
    private LinearLayout.LayoutParams weight(float weight) { return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, weight); }

    private TextView text(String value, float size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return view;
    }

    private TextView label(String value) {
        TextView view = text(value, 13, MUTED, true);
        LinearLayout.LayoutParams params = lp(-1, -2);
        params.topMargin = dp(13);
        params.bottomMargin = dp(5);
        return view;
    }

    private EditText edit(String hint, boolean secret) {
        EditText field = new EditText(this);
        field.setSingleLine(true);
        field.setTextSize(15);
        field.setPadding(dp(12), 0, dp(12), 0);
        field.setHint(hint);
        field.setBackground(round(Color.WHITE, Color.rgb(208, 216, 225), 9));
        if (secret) field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        return field;
    }

    private GradientDrawable round(int fill, int stroke, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(dp(radius));
        if (stroke != Color.TRANSPARENT) shape.setStroke(dp(1), stroke);
        return shape;
    }

    private Button button(String title, boolean primary, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setAllCaps(false);
        button.setTextSize(14);
        button.setOnClickListener(v -> action.run());
        button.setBackground(round(primary ? BLUE : Color.WHITE, primary ? BLUE : Color.rgb(210, 218, 227), 9));
        button.setTextColor(primary ? Color.WHITE : INK);
        return button;
    }

    private View card(View child) {
        LinearLayout shell = column();
        shell.setPadding(dp(18), dp(16), dp(18), dp(18));
        shell.setBackground(round(CARD, Color.rgb(228, 233, 239), 14));
        shell.addView(child, lp(-1, -2));
        return shell;
    }

    private void render() {
        LinearLayout root = column();
        root.setBackgroundColor(Color.rgb(245, 247, 250));
        root.setPadding(dp(18), dp(10), dp(18), dp(12));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left, top, right, bottom;
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                left = bars.left; top = bars.top; right = bars.right; bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft(); top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight(); bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(dp(18) + left, dp(10) + top, dp(18) + right, dp(12) + bottom);
            return insets;
        });
        LinearLayout heading = row();
        LinearLayout titleStack = column();
        titleStack.addView(text("FluxFTP", 23, INK, true));
        titleStack.addView(text("Secure desktop companion", 13, MUTED, false));
        heading.addView(titleStack, weight(1));
        connectionLabel = text(api == null ? "Not connected" : (connected ? "Connected" : "Connecting…"), 13, connected ? Color.rgb(30, 133, 86) : (api == null ? MUTED : BLUE), true);
        heading.addView(connectionLabel);
        root.addView(heading, lp(-1, -2));

        LinearLayout nav = row();
        nav.setPadding(0, dp(12), 0, dp(10));
        String[] pages = {"Queue", "Add transfer", "Settings"};
        for (String destination : pages) {
            Button tab = new Button(this);
            tab.setAllCaps(false);
            tab.setTextSize(13);
            tab.setText(destination);
            tab.setTextColor(page.equals(destination) ? BLUE : MUTED);
            tab.setBackgroundColor(Color.TRANSPARENT);
            tab.setOnClickListener(v -> { page = destination; render(); });
            nav.addView(tab, weight(1));
        }
        root.addView(nav, lp(-1, -2));

        messageLabel = text("", 13, MUTED, false);
        messageLabel.setPadding(dp(3), 0, dp(3), dp(7));
        root.addView(messageLabel, lp(-1, -2));
        ScrollView scroller = new ScrollView(this);
        content = column();
        int width = getResources().getConfiguration().screenWidthDp;
        content.setOrientation(width >= 700 && !page.equals("Settings") ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        content.setGravity(Gravity.TOP);
        scroller.addView(content, new ScrollView.LayoutParams(-1, -2));
        root.addView(scroller, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);

        if (page.equals("Queue")) renderQueue(width >= 700);
        else if (page.equals("Add transfer")) renderTransfer(width >= 700);
        else renderSettings();
    }

    private void renderQueue(boolean wide) {
        LinearLayout left = column();
        left.addView(text("Transfer queue", 19, INK, true));
        TextView summary = text("Waiting for connection…", 14, MUTED, false);
        summary.setPadding(0, dp(9), 0, dp(14));
        left.addView(summary);
        LinearLayout buttons = row();
        buttons.addView(button("Resume", true, () -> post("/v1/queue/play", new JSONObject())), weight(1));
        addGap(buttons, 8);
        buttons.addView(button("Pause", false, () -> post("/v1/queue/pause", new JSONObject())), weight(1));
        addGap(buttons, 8);
        buttons.addView(button("Stop", false, () -> confirmStop()), weight(1));
        left.addView(buttons, lp(-1, -2));
        View leftCard = card(left);

        LinearLayout right = column();
        LinearLayout top = row();
        top.addView(text("Transfers", 18, INK, true), weight(1));
        top.addView(button("Refresh", false, () -> refresh(true)), lp(-2, -2));
        right.addView(top, lp(-1, -2));
        LinearLayout items = column();
        if (transfers.isEmpty()) {
            TextView empty = text("No transfer rows yet. Connect to FluxFTP, then refresh.", 14, MUTED, false);
            empty.setPadding(0, dp(18), 0, dp(6));
            items.addView(empty);
        } else {
            for (Transfer transfer : transfers) items.addView(transferCard(transfer), lp(-1, -2));
        }
        right.addView(items, lp(-1, -2));
        View rightCard = card(right);
        addSection(leftCard, wide);
        addSection(rightCard, wide);
        if (api != null && !transfers.isEmpty()) {
            summary.setText(statusSummary());
        }
    }

    private void renderTransfer(boolean wide) {
        LinearLayout form = column();
        form.addView(text("Queue a transfer", 19, INK, true));
        form.addView(text("Paths are on the FluxFTP desktop host. Use an enabled saved site for a remote endpoint.", 13, MUTED, false));
        form.addView(label("Source site (blank means desktop path)"));
        sourceSite = siteSpinner();
        form.addView(sourceSite, lp(-1, dp(48)));
        form.addView(label("Source path"));
        sourcePath = edit("C:/data/file.zip or /remote/file.zip", false);
        form.addView(sourcePath, lp(-1, dp(48)));
        form.addView(label("Destination site (blank means desktop path)"));
        destinationSite = siteSpinner();
        form.addView(destinationSite, lp(-1, dp(48)));
        form.addView(label("Destination path"));
        destinationPath = edit("D:/incoming/file.zip or /remote/file.zip", false);
        form.addView(destinationPath, lp(-1, dp(48)));
        directoryCheck = new CheckBox(this);
        directoryCheck.setText("Source is a folder (required for a remote source)");
        form.addView(directoryCheck, lp(-1, -2));
        startCheck = new CheckBox(this);
        startCheck.setText("Start immediately (otherwise hold in queue)");
        form.addView(startCheck, lp(-1, -2));
        Button queue = button("Queue transfer", true, () -> queueTransfer());
        LinearLayout.LayoutParams queueLp = lp(-1, dp(48));
        queueLp.topMargin = dp(8);
        form.addView(queue, queueLp);

        LinearLayout right = column();
        LinearLayout top = row();
        top.addView(text("Current queue", 18, INK, true), weight(1));
        top.addView(button("Refresh", false, () -> refresh(true)), lp(-2, -2));
        right.addView(top, lp(-1, -2));
        for (Transfer transfer : transfers) right.addView(transferCard(transfer), lp(-1, -2));
        if (transfers.isEmpty()) right.addView(text("Connect to load the queue.", 14, MUTED, false));
        addSection(card(form), wide);
        addSection(card(right), wide);
        loadSites();
    }

    private void renderSettings() {
        LinearLayout form = column();
        form.addView(text("Connection", 19, INK, true));
        form.addView(text("Enter the local endpoint of your authenticated tunnel and the bearer token from FluxFTP → Tools → JSON API Access.", 14, MUTED, false));
        form.addView(label("API URL"));
        urlField = edit("http://127.0.0.1:8765", false);
        urlField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        String savedUrl = getPreferences(MODE_PRIVATE).getString("api_url", "http://127.0.0.1:8765");
        urlField.setText(savedUrl);
        form.addView(urlField, lp(-1, dp(48)));
        form.addView(label("Bearer token"));
        tokenField = edit("Token stays encrypted on this device", true);
        form.addView(tokenField, lp(-1, dp(48)));
        form.addView(text("Use HTTPS for non-loopback endpoints. For emulator testing, run adb reverse tcp:8765 tcp:8765 and use http://127.0.0.1:8765. Never expose FluxFTP's API port publicly.", 13, MUTED, false));
        Button save = button("Save and connect", true, () -> saveConnection());
        LinearLayout.LayoutParams saveLp = lp(-1, dp(48));
        saveLp.topMargin = dp(14);
        form.addView(save, saveLp);
        Button forget = button("Forget saved connection", false, () -> forgetConnection());
        LinearLayout.LayoutParams forgetLp = lp(-1, dp(46));
        forgetLp.topMargin = dp(8);
        form.addView(forget, forgetLp);
        LinearLayout holder = column();
        holder.addView(card(form), lp(-1, -2));
        content.addView(holder, lp(-1, -2));
    }

    private Spinner siteSpinner() {
        Spinner spinner = new Spinner(this);
        spinner.setBackground(round(Color.WHITE, Color.rgb(208, 216, 225), 9));
        spinner.setPadding(dp(9), 0, dp(9), 0);
        setSpinnerSites(spinner);
        return spinner;
    }

    private void setSpinnerSites(Spinner spinner) {
        List<String> options = new ArrayList<>();
        options.add("Desktop local path");
        options.addAll(sites);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, options);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
    }

    private void selectSite(Spinner spinner, String site) {
        if (spinner == null || spinner.getAdapter() == null) return;
        for (int i = 0; i < spinner.getAdapter().getCount(); i++) {
            if (spinner.getAdapter().getItem(i).toString().equals(site)) { spinner.setSelection(i); return; }
        }
        spinner.setSelection(0);
    }

    private void loadSites() {
        if (api == null) return;
        worker.execute(() -> {
            try {
                JSONObject result = api.get("/v1/sites");
                JSONArray entries = result.optJSONArray("sites");
                List<String> updated = new ArrayList<>();
                if (entries != null) for (int i = 0; i < entries.length(); i++) updated.add(entries.getString(i));
                String selectedSource = sourceSite == null || sourceSite.getSelectedItem() == null ? "Desktop local path" : sourceSite.getSelectedItem().toString();
                String selectedDestination = destinationSite == null || destinationSite.getSelectedItem() == null ? "Desktop local path" : destinationSite.getSelectedItem().toString();
                runOnUiThread(() -> {
                    sites.clear(); sites.addAll(updated);
                    if (sourceSite != null) { setSpinnerSites(sourceSite); selectSite(sourceSite, selectedSource); }
                    if (destinationSite != null) { setSpinnerSites(destinationSite); selectSite(destinationSite, selectedDestination); }
                });
            } catch (Exception error) { showMessage(error.getMessage()); }
        });
    }

    private void addSection(View view, boolean wide) {
        LinearLayout.LayoutParams params = wide ? weight(1) : lp(-1, -2);
        if (wide) {
            params.setMargins(dp(5), 0, dp(5), 0);
        } else {
            params.setMargins(0, 0, 0, dp(12));
        }
        content.addView(view, params);
    }

    private void addGap(LinearLayout row, int size) {
        View gap = new View(this);
        row.addView(gap, lp(dp(size), 1));
    }

    private View transferCard(Transfer transfer) {
        LinearLayout box = column();
        box.setPadding(dp(12), dp(11), dp(12), dp(11));
        box.setBackground(round(Color.rgb(249, 250, 252), Color.rgb(233, 237, 242), 10));
        LinearLayout.LayoutParams params = lp(-1, -2);
        params.topMargin = dp(8);
        box.addView(text(transfer.name.isEmpty() ? "Transfer" : transfer.name, 15, INK, true), lp(-1, -2));
        box.addView(text(transfer.source + "  →  " + transfer.destination, 12, MUTED, false), lp(-1, -2));
        LinearLayout details = row();
        details.addView(text(transfer.state + (transfer.progress.isEmpty() ? "" : "  ·  " + transfer.progress), 12, BLUE, true), weight(1));
        details.addView(text(transfer.speed, 12, MUTED, false));
        box.addView(details, lp(-1, -2));
        if (!transfer.id.isEmpty()) {
            Button cancel = button("Cancel", false, () -> cancelTransfer(transfer.id));
            LinearLayout.LayoutParams cancelLp = lp(-2, dp(40));
            cancelLp.topMargin = dp(6);
            box.addView(cancel, cancelLp);
        }
        return box;
    }

    private void saveConnection() {
        String url = urlField.getText().toString().trim();
        String token = tokenField.getText().toString().trim();
        if (token.isEmpty() && api == null) { showMessage("Enter the bearer token from FluxFTP's JSON API Access dialog."); return; }
        if (token.isEmpty() && api != null) token = api.token;
        try {
            ApiClient checked = new ApiClient(url, token);
            connected = false;
            new TokenVault(this).write(token);
            getPreferences(MODE_PRIVATE).edit().putString("api_url", checked.baseUrl).apply();
            api = checked;
            connectionLabel.setText("Connecting…");
            refresh(true);
        } catch (Exception error) { showMessage(error.getMessage()); }
    }

    private void forgetConnection() {
        new TokenVault(this).clear();
        getPreferences(MODE_PRIVATE).edit().remove("api_url").apply();
        api = null;
        transfers.clear();
        render();
        showMessage("Saved connection removed.");
    }

    private void refresh(boolean announce) {
        if (api == null || busy) return;
        busy = true;
        worker.execute(() -> {
            try {
                JSONObject status = api.get("/v1/status");
                JSONObject transferResponse = api.get("/v1/transfers");
                JSONArray items = transferResponse.optJSONArray("transfers");
                List<Transfer> updated = new ArrayList<>();
                if (items != null) for (int i = 0; i < items.length(); i++) updated.add(Transfer.from(items.getJSONObject(i)));
                runOnUiThread(() -> {
                    busy = false;
                    transfers.clear(); transfers.addAll(updated);
                    connected = true;
                    connectionLabel.setText("Connected"); connectionLabel.setTextColor(Color.rgb(30, 133, 86));
                    if (page.equals("Queue")) {
                        render();
                        TextView summary = findSummary();
                        if (summary != null) summary.setText(summary(status));
                    } else if (page.equals("Add transfer")) {
                        String srcText = sourcePath == null ? "" : sourcePath.getText().toString();
                        String dstText = destinationPath == null ? "" : destinationPath.getText().toString();
                        String srcSite = sourceSite == null || sourceSite.getSelectedItem() == null ? "Desktop local path" : sourceSite.getSelectedItem().toString();
                        String dstSite = destinationSite == null || destinationSite.getSelectedItem() == null ? "Desktop local path" : destinationSite.getSelectedItem().toString();
                        boolean directory = directoryCheck != null && directoryCheck.isChecked();
                        boolean start = startCheck != null && startCheck.isChecked();
                        render();
                        sourcePath.setText(srcText); destinationPath.setText(dstText);
                        selectSite(sourceSite, srcSite); selectSite(destinationSite, dstSite);
                        directoryCheck.setChecked(directory); startCheck.setChecked(start);
                    }
                    if (announce) showMessage("Queue refreshed.");
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    busy = false;
                    connected = false;
                    connectionLabel.setText("Not connected"); connectionLabel.setTextColor(Color.rgb(175, 66, 58));
                    if (announce) showMessage(error.getMessage());
                });
            }
        });
    }

    private TextView findSummary() {
        if (content == null || content.getChildCount() == 0) return null;
        View first = content.getChildAt(0);
        if (!(first instanceof LinearLayout)) return null;
        LinearLayout shell = (LinearLayout) first;
        if (shell.getChildCount() == 0 || !(shell.getChildAt(0) instanceof LinearLayout)) return null;
        LinearLayout inner = (LinearLayout) shell.getChildAt(0);
        return inner.getChildCount() > 1 && inner.getChildAt(1) instanceof TextView ? (TextView) inner.getChildAt(1) : null;
    }

    private String summary(JSONObject value) {
        return value.optInt("queued") + " waiting  ·  " + value.optInt("active") + " active  ·  " + value.optInt("failed") + " failed  ·  " + (value.optBoolean("paused") ? "paused" : "running");
    }

    private String statusSummary() { return "Queue connected"; }

    private void queueTransfer() {
        if (api == null) { showMessage("Connect to FluxFTP first in Settings."); return; }
        String src = sourcePath.getText().toString().trim();
        String dst = destinationPath.getText().toString().trim();
        if (src.isEmpty() || dst.isEmpty()) { showMessage("Enter both source and destination paths."); return; }
        try {
            JSONObject source = endpoint(sourceSite, src);
            JSONObject destination = endpoint(destinationSite, dst);
            JSONObject payload = new JSONObject().put("source", source).put("destination", destination)
                .put("directory", directoryCheck.isChecked()).put("start", startCheck.isChecked());
            post("/v1/transfers", payload);
        } catch (Exception error) { showMessage(error.getMessage()); }
    }

    private JSONObject endpoint(Spinner spinner, String path) throws Exception {
        JSONObject endpoint = new JSONObject().put("path", path);
        Object value = spinner.getSelectedItem();
        if (value != null && !value.toString().equals("Desktop local path")) endpoint.put("site", value.toString());
        return endpoint;
    }

    private void post(String path, JSONObject payload) {
        if (api == null) { showMessage("Connect to FluxFTP first in Settings."); return; }
        worker.execute(() -> {
            try {
                api.post(path, payload);
                runOnUiThread(() -> { showMessage("Command sent."); refresh(false); });
            } catch (Exception error) { showMessage(error.getMessage()); }
        });
    }

    private void cancelTransfer(String id) {
        try { post("/v1/queue/cancel", new JSONObject().put("ids", new JSONArray().put(id))); }
        catch (Exception error) { showMessage(error.getMessage()); }
    }

    private void confirmStop() {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Stop the queue?")
            .setMessage("This stops active work and clears pending work on the FluxFTP desktop.")
            .setNegativeButton("Keep running", null)
            .setPositiveButton("Stop queue", (dialog, which) -> post("/v1/queue/stop", new JSONObject()))
            .show();
    }

    private void showMessage(String message) {
        if (Looper.myLooper() != Looper.getMainLooper()) { runOnUiThread(() -> showMessage(message)); return; }
        if (messageLabel != null) messageLabel.setText(message == null ? "Request failed." : message);
    }

    private static final class Transfer {
        final String id, name, source, destination, state, progress, speed;
        Transfer(String id, String name, String source, String destination, String state, String progress, String speed) {
            this.id=id; this.name=name; this.source=source; this.destination=destination; this.state=state; this.progress=progress; this.speed=speed;
        }
        static Transfer from(JSONObject json) {
            return new Transfer(json.optString("id"), json.optString("name"), json.optString("source"), json.optString("destination"),
                json.optString("state"), json.optString("progress"), json.optString("speed"));
        }
    }

    private static final class TokenVault {
        private static final String KEY_ALIAS = "fluxftp_companion_token_v1";
        private final SharedPreferences preferences;
        TokenVault(Context context) { preferences = context.getSharedPreferences("secure_connection", Context.MODE_PRIVATE); }
        String read() {
            try {
                String encoded = preferences.getString("token", "");
                if (encoded.isEmpty()) return "";
                byte[] data = Base64.getDecoder().decode(encoded);
                byte[] iv = new byte[12];
                System.arraycopy(data, 0, iv, 0, iv.length);
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
                byte[] encrypted = new byte[data.length - iv.length];
                System.arraycopy(data, iv.length, encrypted, 0, encrypted.length);
                return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
            } catch (Exception ignored) { return ""; }
        }
        void write(String value) throws Exception {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] iv = cipher.getIV();
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            byte[] joined = new byte[iv.length + encrypted.length];
            System.arraycopy(iv, 0, joined, 0, iv.length);
            System.arraycopy(encrypted, 0, joined, iv.length, encrypted.length);
            preferences.edit().putString("token", Base64.getEncoder().encodeToString(joined)).apply();
        }
        void clear() { preferences.edit().remove("token").apply(); }
        private SecretKey key() throws Exception {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
            if (store.containsAlias(KEY_ALIAS)) return (SecretKey) store.getKey(KEY_ALIAS, null);
            KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
            android.security.keystore.KeyGenParameterSpec spec = new android.security.keystore.KeyGenParameterSpec.Builder(KEY_ALIAS,
                android.security.keystore.KeyProperties.PURPOSE_ENCRYPT | android.security.keystore.KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build();
            generator.init(spec);
            return generator.generateKey();
        }
    }

    private static final class ApiClient {
        final String baseUrl;
        final String token;
        ApiClient(String endpoint, String token) {
            URI uri = URI.create(endpoint.trim());
            if (uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null)
                throw new IllegalArgumentException("Enter a valid API base URL without username, query, or fragment.");
            String scheme = uri.getScheme();
            String host = uri.getHost().toLowerCase();
            boolean loopback = host.equals("localhost") || host.equals("127.0.0.1") || host.equals("::1") || host.equals("10.0.2.2");
            if ("http".equalsIgnoreCase(scheme) && !loopback) throw new IllegalArgumentException("Plain HTTP is only allowed to localhost. Use HTTPS through your private tunnel for other hosts.");
            if (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme)) throw new IllegalArgumentException("API URL must use HTTP loopback or HTTPS.");
            String normalized = endpoint.trim().replaceAll("/+$", "");
            if (normalized.isEmpty()) throw new IllegalArgumentException("Enter the API URL.");
            if (token == null || token.trim().isEmpty()) throw new IllegalArgumentException("Enter the bearer token.");
            this.baseUrl = normalized; this.token = token.trim();
        }
        JSONObject get(String path) throws Exception { return request("GET", path, null); }
        JSONObject post(String path, JSONObject body) throws Exception { return request("POST", path, body); }
        JSONObject request(String method, String path, JSONObject body) throws Exception {
            URL url = new URL(baseUrl + path);
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setRequestMethod(method); connection.setConnectTimeout(5000); connection.setReadTimeout(10000);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.getOutputStream().write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            if (stream != null) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = stream.read(buffer)) != -1) bytes.write(buffer, 0, count);
                stream.close();
            }
            connection.disconnect();
            String response = new String(bytes.toByteArray(), StandardCharsets.UTF_8);
            JSONObject result = response.isEmpty() ? new JSONObject() : new JSONObject(response);
            if (code < 200 || code >= 300) throw new IllegalStateException(result.optString("error", "FluxFTP returned HTTP " + code));
            return result;
        }
    }
}
