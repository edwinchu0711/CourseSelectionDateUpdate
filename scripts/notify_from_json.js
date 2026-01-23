// scripts/notify_from_json.js
const axios = require('axios');
const { google } = require('googleapis');

// 1. 設定：你的 JSON 網址
const JSON_URL = "https://edwinchu0711.github.io/CourseSelectionDateUpdate/data.json";

// 2. 從環境變數讀取 Firebase Service Account (這是唯一需要的 Firebase 憑證)
// 請去 Firebase Console -> Project Settings -> Service Accounts -> Generate Private Key
// 把下載下來的 JSON 內容，貼到 GitHub Repository Secrets，命名為 FIREBASE_KEY
const serviceAccount = JSON.parse(process.env.FIREBASE_KEY);

// 3. 取得 FCM 授權 Token (Google HTTP v1 API)
function getAccessToken() {
  return new Promise(function(resolve, reject) {
    const jwtClient = new google.auth.JWT(
      serviceAccount.client_email,
      null,
      serviceAccount.private_key,
      ['https://www.googleapis.com/auth/firebase.messaging'],
      null
    );
    jwtClient.authorize(function(err, tokens) {
      if (err) {
        reject(err);
        return;
      }
      resolve(tokens.access_token);
    });
  });
}

async function main() {
  try {
    // A. 下載 JSON
    console.log("正在下載資料...");
    const response = await axios.get(JSON_URL);
    const data = response.data.data; // 根據你的 JSON 結構調整

    // B. 解析日期並檢查
    const now = new Date();
    // 轉成台灣時間 (UTC+8) 的 "明天"
    const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000 + 8 * 60 * 60 * 1000); 
    const tomorrowStr = tomorrow.toISOString().split('T')[0]; // 格式: YYYY-MM-DD
    
    // 簡單的日期比對邏輯 (需根據你的資料格式微調)
    // 假設你的資料 key 或 value 裡有日期字串
    let messageBody = "";

    // --- 這裡要寫你的邏輯 ---
    // 範例：遍歷 data，看哪一個項目的 '開始時間' 符合 tomorrowStr
    for (const [key, value] of Object.entries(data)) {
        if (value['開始時間'] && value['開始時間'].includes(tomorrowStr)) { // 粗略比對，請依實際格式調整
            messageBody = `明天是 ${key}，記得看時間喔！`;
            break;
        }
    }
    // -----------------------

    // C. 如果有訊息要發，就呼叫 FCM
    if (messageBody) {
      console.log(`準備發送: ${messageBody}`);
      const accessToken = await getAccessToken();
      
      const payload = {
        message: {
          topic: "all_users", // 發給所有訂閱的人
          notification: {
            title: "選課提醒",
            body: messageBody,
          }
        }
      };

      await axios.post(
        `https://fcm.googleapis.com/v1/projects/${serviceAccount.project_id}/messages:send`,
        payload,
        {
          headers: {
            'Authorization': `Bearer ${accessToken}`,
            'Content-Type': 'application/json'
          }
        }
      );
      console.log("✅ 推播發送成功！");
    } else {
      console.log("📅 明天沒有活動，不需要發送通知。");
    }

  } catch (error) {
    console.error("❌ 發生錯誤:", error);
    process.exit(1);
  }
}

main();
