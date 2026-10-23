package app.prisme.tv;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Prisme TV : la bibliothèque de Prisme sur la télé.
 *
 * <p>Au premier lancement, l'application cherche Prisme sur le Wi-Fi de la
 * maison (voir videosorter/tvlink.py) et demande l'accès ; le PC pose la
 * question, et la réponse porte le lien d'invitation. Il est gardé : les fois
 * suivantes, la bibliothèque s'ouvre directement. La page elle-même vient du
 * PC ; elle se reconnaît « PrismeTV » et se conduit à la télécommande.
 */
public class MainActivity extends Activity {

    private static final int PORT = 8714;
    private static final String PREFS = "prisme";
    private static final String KEY_URL = "adresse";
    private static final int ACCENT = Color.parseColor("#4f8bf0");
    private static final int INK = Color.parseColor("#e9eef4");
    private static final int DIM = Color.parseColor("#8b94a1");
    private static final int BACKGROUND = Color.parseColor("#0b0d10");

    private WebView web;
    private LinearLayout found;
    private TextView status;
    private volatile int generation;     // une recherche ou une demande chasse l'autre

    // -- cycle de vie -----------------------------------------------------------

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        String url = prefs().getString(KEY_URL, "");
        if (url.isEmpty()) {
            showPairing();
        } else {
            showLibrary(url);
        }
    }

    @Override
    protected void onDestroy() {
        generation++;
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) {
            web.onPause();       // la vidéo s'arrête quand on quitte l'application
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) {
            web.onResume();
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    // -- la bibliothèque ----------------------------------------------------------

    private void showLibrary(String url) {
        generation++;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (web == null) {
            web = new WebView(this);
            web.setBackgroundColor(BACKGROUND);
            WebSettings settings = web.getSettings();
            settings.setJavaScriptEnabled(true);
            settings.setDomStorageEnabled(true);
            settings.setMediaPlaybackRequiresUserGesture(false);
            settings.setUserAgentString(settings.getUserAgentString() + " PrismeTV/1.0");
            web.setWebViewClient(new WebViewClient() {
                @Override
                public boolean shouldOverrideUrlLoading(WebView view, String address) {
                    return false;
                }

                @Override
                public void onReceivedError(WebView view, WebResourceRequest request,
                                            WebResourceError error) {
                    if (Build.VERSION.SDK_INT >= 23 && request.isForMainFrame()) {
                        showUnreachable();
                    }
                }

                @Override
                @SuppressWarnings("deprecation")
                public void onReceivedError(WebView view, int code, String description,
                                            String failing) {
                    if (Build.VERSION.SDK_INT < 23) {
                        showUnreachable();
                    }
                }
            });
        }
        setContentView(web);
        web.requestFocus();
        web.loadUrl(url);
    }

    private void showUnreachable() {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout page = page();
        page.addView(title("Prisme ne répond pas"));
        page.addView(text("Le PC est-il allumé, sans être en veille, avec Prisme ouvert "
                + "et le partage à distance activé ? La télé et le PC doivent être "
                + "sur le même réseau.", DIM));
        Button retry = button("Réessayer");
        retry.setOnClickListener(v -> showLibrary(prefs().getString(KEY_URL, "")));
        page.addView(retry);
        Button other = button("Chercher Prisme à nouveau");
        other.setOnClickListener(v -> forget());
        page.addView(other);
        show(page);
        retry.requestFocus();
    }

    private void forget() {
        prefs().edit().remove(KEY_URL).apply();
        if (web != null) {
            web.clearHistory();
            web.loadUrl("about:blank");
        }
        showPairing();
    }

    // -- l'appairage ---------------------------------------------------------------

    private void showPairing() {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout page = page();
        page.addView(title("Prisme TV"));
        status = text("Recherche de Prisme sur le réseau de la maison…", DIM);
        page.addView(status);
        found = new LinearLayout(this);
        found.setOrientation(LinearLayout.VERTICAL);
        page.addView(found);
        Button again = button("Chercher à nouveau");
        again.setOnClickListener(v -> discover());
        page.addView(again);
        Button manual = button("Saisir l'adresse à la main…");
        manual.setOnClickListener(v -> askAddress());
        page.addView(manual);
        page.addView(text("Sur le PC : Prisme ouvert, et le partage à distance activé "
                + "(⋯ › Connexion › Partage à distance). Prisme vous demandera "
                + "d'autoriser cette télé.", DIM));
        show(page);
        again.requestFocus();
        discover();
    }

    private void discover() {
        final int mine = ++generation;
        found.removeAllViews();
        status.setText("Recherche de Prisme sur le réseau de la maison…");
        new Thread(() -> {
            Map<String, JSONObject> pcs = new LinkedHashMap<>();
            WifiManager.MulticastLock lock = multicastLock();
            try (DatagramSocket socket = new DatagramSocket()) {
                socket.setBroadcast(true);
                socket.setSoTimeout(600);
                byte[] hello = "{\"prisme\":\"hello\",\"v\":1}".getBytes(StandardCharsets.UTF_8);
                long end = System.currentTimeMillis() + 6000;
                long nextShout = 0;
                while (System.currentTimeMillis() < end && generation == mine) {
                    if (System.currentTimeMillis() >= nextShout) {
                        for (InetAddress target : broadcastTargets()) {
                            try {
                                socket.send(new DatagramPacket(hello, hello.length, target, PORT));
                            } catch (Exception ignored) {
                                // une interface sans réseau : on essaie les autres
                            }
                        }
                        nextShout = System.currentTimeMillis() + 1500;
                    }
                    DatagramPacket packet = new DatagramPacket(new byte[2048], 2048);
                    try {
                        socket.receive(packet);
                    } catch (SocketTimeoutException timeout) {
                        continue;
                    }
                    JSONObject answer = parse(packet);
                    if (answer != null && "here".equals(answer.optString("prisme"))) {
                        String ip = packet.getAddress().getHostAddress();
                        if (!pcs.containsKey(ip)) {
                            pcs.put(ip, answer);
                            runOnUiThread(() -> {
                                if (generation == mine) addFound(ip, answer);
                            });
                        }
                    }
                }
            } catch (Exception e) {
                runOnUiThread(() -> {
                    if (generation == mine) status.setText("Recherche impossible : " + e.getMessage());
                });
            } finally {
                if (lock != null) lock.release();
            }
            runOnUiThread(() -> {
                if (generation != mine) return;
                status.setText(pcs.isEmpty()
                        ? "Aucun Prisme trouvé. Vérifiez que le partage est activé sur le PC, "
                          + "ou saisissez son adresse à la main."
                        : "Choisissez votre PC :");
            });
        }, "prisme-recherche").start();
    }

    private void addFound(String ip, JSONObject answer) {
        String name = answer.optString("name", "PC");
        int port = answer.optInt("port", 8713);
        Button pc = button("Prisme sur « " + name + " »   " + ip);
        pc.setOnClickListener(v -> pair(ip, port, name));
        found.addView(pc);
        if (found.getChildCount() == 1) pc.requestFocus();
    }

    private void pair(String ip, int port, String name) {
        final int mine = ++generation;
        status.setText("Sur le PC « " + name + " », acceptez la demande de Prisme…");
        found.removeAllViews();
        String nonce = UUID.randomUUID().toString();
        String device = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        new Thread(() -> {
            String outcome = "timeout";
            String url = "";
            try (DatagramSocket socket = new DatagramSocket()) {
                socket.setSoTimeout(1000);
                JSONObject ask = new JSONObject();
                ask.put("prisme", "pair");
                ask.put("device", device);
                ask.put("nonce", nonce);
                byte[] data = ask.toString().getBytes(StandardCharsets.UTF_8);
                InetAddress pc = InetAddress.getByName(ip);
                long end = System.currentTimeMillis() + 90000;
                long nextAsk = 0;
                while (System.currentTimeMillis() < end && generation == mine) {
                    if (System.currentTimeMillis() >= nextAsk) {
                        socket.send(new DatagramPacket(data, data.length, pc, PORT));
                        nextAsk = System.currentTimeMillis() + 2500;
                    }
                    DatagramPacket packet = new DatagramPacket(new byte[2048], 2048);
                    try {
                        socket.receive(packet);
                    } catch (SocketTimeoutException timeout) {
                        continue;
                    }
                    JSONObject answer = parse(packet);
                    String kind = answer == null ? "" : answer.optString("prisme");
                    if ("paired".equals(kind)) {
                        int served = answer.optInt("port", port);
                        url = "http://" + ip + ":" + served + answer.optString("path", "/");
                        outcome = "paired";
                        break;
                    }
                    if ("refused".equals(kind)) {
                        outcome = "refused";
                        break;
                    }
                }
            } catch (Exception e) {
                outcome = "error";
            }
            final String result = outcome, address = url;
            runOnUiThread(() -> {
                if (generation != mine) return;
                if ("paired".equals(result)) {
                    prefs().edit().putString(KEY_URL, address).apply();
                    showLibrary(address);
                } else {
                    status.setText("refused".equals(result)
                            ? "La demande a été refusée sur le PC."
                            : "Pas de réponse du PC. Choisissez « Chercher à nouveau ».");
                }
            });
        }, "prisme-appairage").start();
    }

    private void askAddress() {
        EditText field = new EditText(this);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        field.setHint("192.168.1.20, ou https://…");
        new AlertDialog.Builder(this)
                .setTitle("Adresse de Prisme")
                .setMessage("L'adresse du PC sur le réseau de la maison, ou le lien "
                        + "d'invitation donné par Prisme (Partage à distance).")
                .setView(field)
                .setPositiveButton("Ouvrir", (dialog, which) -> {
                    String url = normalise(field.getText().toString());
                    if (!url.isEmpty()) {
                        prefs().edit().putString(KEY_URL, url).apply();
                        showLibrary(url);
                    }
                })
                .setNegativeButton("Annuler", null)
                .show();
    }

    /** « 192.168.1.20 » → « http://192.168.1.20:8713/ » ; un lien complet reste tel quel. */
    static String normalise(String typed) {
        String text = typed.trim();
        if (text.isEmpty()) return "";
        if (!text.contains("://")) {
            String host = text.contains("/") ? text.substring(0, text.indexOf('/')) : text;
            String rest = text.contains("/") ? text.substring(text.indexOf('/')) : "/";
            if (!host.contains(":")) host += ":8713";
            text = "http://" + host + rest;
        }
        return text;
    }

    // -- réseau ----------------------------------------------------------------------

    private WifiManager.MulticastLock multicastLock() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi == null) return null;
            WifiManager.MulticastLock lock = wifi.createMulticastLock("prisme");
            lock.setReferenceCounted(false);
            lock.acquire();
            return lock;
        } catch (Exception e) {
            return null;
        }
    }

    /** L'adresse de diffusion du Wi-Fi, et celle de tout le monde. */
    @SuppressWarnings("deprecation")
    private Iterable<InetAddress> broadcastTargets() throws Exception {
        java.util.List<InetAddress> targets = new java.util.ArrayList<>();
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null && wifi.getDhcpInfo() != null && wifi.getDhcpInfo().ipAddress != 0) {
                int ip = wifi.getDhcpInfo().ipAddress;
                int mask = wifi.getDhcpInfo().netmask;
                int broadcast = (ip & mask) | ~mask;
                byte[] quad = new byte[4];
                for (int k = 0; k < 4; k++) quad[k] = (byte) (broadcast >> (k * 8));
                targets.add(InetAddress.getByAddress(quad));
            }
        } catch (Exception ignored) {
            // par câble, pas de Wi-Fi : la diffusion générale suffit
        }
        targets.add(InetAddress.getByName("255.255.255.255"));
        return targets;
    }

    private static JSONObject parse(DatagramPacket packet) {
        try {
            return new JSONObject(new String(packet.getData(), 0, packet.getLength(),
                    StandardCharsets.UTF_8));
        } catch (Exception e) {
            return null;
        }
    }

    // -- la télécommande -----------------------------------------------------------------

    @Override
    public void onBackPressed() {
        if (web == null || web.getParent() == null) {
            super.onBackPressed();
            return;
        }
        // La page sait revenir d'un dossier ou fermer le lecteur ; sinon, on sort.
        web.evaluateJavascript("window.prismeBack ? String(window.prismeBack()) : 'false'",
                value -> {
                    if (!"\"true\"".equals(value)) finish();
                });
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        // Le bouton Menu (☰) : changer de PC.
        if (keyCode == KeyEvent.KEYCODE_MENU && web != null && web.getParent() != null) {
            new AlertDialog.Builder(this)
                    .setTitle("Prisme TV")
                    .setItems(new String[]{"Recharger", "Changer de PC (oublier celui-ci)"},
                            (dialog, which) -> {
                                if (which == 0) web.reload();
                                else forget();
                            })
                    .show();
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }

    // -- l'habillage ---------------------------------------------------------------------

    private LinearLayout page() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setGravity(Gravity.CENTER_HORIZONTAL);
        page.setBackgroundColor(BACKGROUND);
        int pad = dp(48);
        page.setPadding(pad * 2, pad, pad * 2, pad);
        return page;
    }

    private void show(View page) {
        if (web != null) web.stopLoading();
        setContentView(page);
    }

    private TextView title(String text) {
        TextView view = text(text, INK);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 32);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    private TextView text(String text, int color) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(color);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, dp(10), 0, dp(10));
        view.setMaxWidth(dp(720));
        return view;
    }

    private Button button(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextColor(INK);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        StateListDrawable states = new StateListDrawable();
        states.addState(new int[]{android.R.attr.state_focused}, rounded(ACCENT));
        states.addState(new int[]{}, rounded(Color.parseColor("#1a1f27")));
        button.setBackground(states);
        button.setPadding(dp(28), dp(14), dp(28), dp(14));
        button.setFocusable(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(12);
        params.gravity = Gravity.CENTER_HORIZONTAL;
        button.setLayoutParams(params);
        return button;
    }

    private GradientDrawable rounded(int color) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(10));
        return shape;
    }

    private int dp(int value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value,
                getResources().getDisplayMetrics()));
    }
}
