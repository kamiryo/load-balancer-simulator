/**
 * =========================================================================
 * 負荷分散シミュレータ 外部設定・定義ファイル (config.js)
 * =========================================================================
 * サーバ名、IPアドレス、負荷分散NW装置、運用ジョブの定義をここで集中管理します。
 * 本ファイルを編集することで、index.html の本体コードを変更することなく、
 * システム構成や表示名、IPアドレス、ジョブ名、待機時間などを自由にカスタマイズ可能です。
 */

window.SIM_CONFIG = {
  // =========================================================================
  // 1. 負荷分散ネットワーク装置 (External LB, Internal LB VIP-A/B, DB)
  // =========================================================================
  networkDevices: {
    // 外部フロント負荷分散装置 (Web代表 VIP)
    externalLb: {
      title: "2. External LB",
      name: "Web代表 VIP",
      ip: "192.168.1.10:443",
      labelA: "Web A系:",
      labelB: "Web B系:",
      labelSorry: "SORRY:"
    },

    // 内部負荷分散装置 (AP代表 VIP / VIP-A, VIP-B)
    internalLb: {
      title: "4. Internal LB",
      vipA: {
        name: "AP-A系代表 VIP",
        ip: "192.168.10.10:8080",
        routingNote: "Web A系 ➜ AP A系(02,04) / Web単位固定(30s)"
      },
      vipB: {
        name: "AP-B系代表 VIP",
        ip: "192.168.20.10:8080",
        routingNote: "Web B系 ➜ AP B系(01,03) / Web単位固定(30s)"
      }
    },

    // バックエンド データベース (Database)
    database: {
      title: "6. DB層 (共通)",
      name: "Database",
      subTitle: "SQL実行 & レスポンス生成"
    }
  },

  // =========================================================================
  // 2. サーバ定義 (Web層、SORRY、AP層)
  // =========================================================================
  servers: {
    // Webサーバ群 (A系: 偶数, B系: 奇数, SORRY)
    web: [
      {
        id: "web_02",
        name: "Web-02",
        ip: "10.0.1.12",
        system: "A",
        initialStatus: "up/active",
        defaultStaySec: 0.5
      },
      {
        id: "web_04",
        name: "Web-04",
        ip: "10.0.1.14",
        system: "A",
        initialStatus: "up/active",
        defaultStaySec: 0.5
      },
      {
        id: "web_01",
        name: "Web-01",
        ip: "10.0.2.11",
        system: "B",
        initialStatus: "up/active",
        defaultStaySec: 0.5
      },
      {
        id: "web_03",
        name: "Web-03",
        ip: "10.0.2.13",
        system: "B",
        initialStatus: "up/active",
        defaultStaySec: 0.5
      },
      {
        id: "web_sorry",
        name: "SORRY",
        ip: "10.0.0.99",
        system: "SORRY",
        initialStatus: "up/standby",
        defaultStaySec: 0.5
      }
    ],

    // AP (Application) サーバ群 (Java WAR / 4台構成: A系 偶数2台, B系 奇数2台)
    ap: [
      {
        id: "ap_02",
        name: "AP-02",
        ip: "10.0.10.22",
        system: "A",
        version: "v1.0.0",
        initialStatus: "up/active",
        defaultStaySec: 1.0
      },
      {
        id: "ap_04",
        name: "AP-04",
        ip: "10.0.10.24",
        system: "A",
        version: "v1.0.0",
        initialStatus: "up/active",
        defaultStaySec: 1.0
      },
      {
        id: "ap_01",
        name: "AP-01",
        ip: "10.0.20.21",
        system: "B",
        version: "v1.0.0",
        initialStatus: "up/active",
        defaultStaySec: 1.0
      },
      {
        id: "ap_03",
        name: "AP-03",
        ip: "10.0.20.23",
        system: "B",
        version: "v1.0.0",
        initialStatus: "up/active",
        defaultStaySec: 1.0
      }
    ]
  },

  // =========================================================================
  // 3. 実務運用ジョブ定義 (9種)
  // =========================================================================
  jobs: [
    // --- 【A系 WEBジョブ】 ---
    {
      id: "job-web-a-cut-wait",
      group: "A",
      groupTitle: "【A系 WEBジョブ】",
      targetSummary: "(Web-02, Web-04)",
      name: "WEB-A系切離-waitあり",
      action: "cut-wait",
      targetSystem: "A",
      waitDuration: 30,
      badge: "30s維持",
      btnStyle: "wait" // 琥珀色
    },
    {
      id: "job-web-a-cut-nowait",
      group: "A",
      groupTitle: "【A系 WEBジョブ】",
      targetSummary: "(Web-02, Web-04)",
      name: "WEB-A系切離-waitなし",
      action: "cut-nowait",
      targetSystem: "A",
      badge: "即時切断",
      btnStyle: "danger" // 赤色
    },
    {
      id: "job-web-a-add",
      group: "A",
      groupTitle: "【A系 WEBジョブ】",
      targetSummary: "(Web-02, Web-04)",
      name: "WEB-A系組込",
      action: "integrate",
      targetSystem: "A",
      badge: "up/active復帰",
      btnStyle: "success" // 緑色
    },

    // --- 【B系 WEBジョブ】 ---
    {
      id: "job-web-b-cut-wait",
      group: "B",
      groupTitle: "【B系 WEBジョブ】",
      targetSummary: "(Web-01, Web-03)",
      name: "WEB-B系切離-waitあり",
      action: "cut-wait",
      targetSystem: "B",
      waitDuration: 30,
      badge: "30s維持",
      btnStyle: "wait"
    },
    {
      id: "job-web-b-cut-nowait",
      group: "B",
      groupTitle: "【B系 WEBジョブ】",
      targetSummary: "(Web-01, Web-03)",
      name: "WEB-B系切離-waitなし",
      action: "cut-nowait",
      targetSystem: "B",
      badge: "即時切断",
      btnStyle: "danger"
    },
    {
      id: "job-web-b-add",
      group: "B",
      groupTitle: "【B系 WEBジョブ】",
      targetSummary: "(Web-01, Web-03)",
      name: "WEB-B系組込",
      action: "integrate",
      targetSystem: "B",
      badge: "up/active復帰",
      btnStyle: "success"
    },

    // --- 【WEB 全台ジョブ】 ---
    {
      id: "job-web-all-cut-wait",
      group: "ALL",
      groupTitle: "【WEB 全台ジョブ】(全滅でSORRY発動)",
      targetSummary: "",
      name: "WEB全台切離-waitあり",
      action: "cut-all-wait",
      targetSystem: "ALL",
      waitDuration: 30,
      badge: "30s後SORRY",
      btnStyle: "wait"
    },
    {
      id: "job-web-all-cut-nowait",
      group: "ALL",
      groupTitle: "【WEB 全台ジョブ】(全滅でSORRY発動)",
      targetSummary: "",
      name: "WEB全台切離-waitなし",
      action: "cut-all-nowait",
      targetSystem: "ALL",
      badge: "即時SORRY発動",
      btnStyle: "danger"
    },
    {
      id: "job-web-all-add",
      group: "ALL",
      groupTitle: "【WEB 全台ジョブ】(全滅でSORRY発動)",
      targetSummary: "",
      name: "WEB全台組込",
      action: "integrate-all",
      targetSystem: "ALL",
      badge: "全台復帰",
      btnStyle: "success"
    }
  ]
};
