"use strict";
bootExposurePage({
  key: "jam",
  title: "Avoid jamming",
  subtitle: "where your links survive vs get jammed",
  lens: "jamshadow", endpoint: "exposure", polarity: "both",
  placeRoles: [{ role: "jammer", label: "jammer", erps: ["low", "medium", "high", "very_high"], defErp: "medium" }],
  autoSource: true,
  bandLabel: "Your links to keep alive",
  presets: {
    GNSS: ["gnss_l1_gps_glonass"],
    FPV: ["ism_433", "ism_868_915", "wifi_bt_rc_fpv_2g4", "wifi_fpv_5g8"],
    comms: ["gsm_cellular_800_960", "gsm_cellular_1800_1900", "satcom_lband_inmarsat_iridium"],
  },
  defaultBands: ["wifi_bt_rc_fpv_2g4", "wifi_fpv_5g8"],
  assetLabel: "Protected asset / area",
  colors: { safe: "#2ecc71", danger: "#e74c3c" },
  legend: { safe: "link survives (terrain weakens jammer)", danger: "can be jammed (jammer reaches you)" },
  disclaimer: "Relative terrain attenuation of the jammer path — NOT jam-proof. High-ERP jammers burn through; shadows never zero; no dBm.",
});
