// scripts/notify_from_json.js
const axios = require('axios');
const { google } = require('googleapis');

// 1. 設定：你的 JSON 網址
const JSON_URL = "https://edwinchu0711.github.io/CourseSelectionDateUpdate/data.json";

// 2. 從環境變數讀取 Firebase Service Account
const serviceAccount = JSON.parse(process.env.FIREBASE_KEY);

// ... 前面的 code ...

// 2. 從環境變數讀取
const envKey = process.env.FIREBASE_KEY;

if (!envKey) {
    console.error("❌ 嚴重錯誤: 找不到環境變數 FIREBASE_KEY");
    console.error("請檢查 GitHub Actions YAML 檔是否漏了 'env: FIREBASE_KEY: ${{ secrets.FIREBASE_KEY }}'");
    process.exit(1);
}

let serviceAccount;
try {
    serviceAccount = JSON.parse(envKey);
} catch (e) {
    console.error("❌ JSON 解析失敗，你的 Secret 可能格式錯誤 (不是有效的 JSON)");
    process.exit(1);
}

// 檢查關鍵欄位是否存在
if (!serviceAccount.project_id) console.error("⚠️ 警告: 缺少 project_id");
if (!serviceAccount.client_email) console.error("⚠️ 警告: 缺少 client_email");
if (!serviceAccount.private_key) {
    console.error("❌ 嚴重錯誤: JSON 內缺少 'private_key' 欄位！");
    process.exit(1);
} else {
    console.log("✅ 成功讀取 Service Account，Project ID:", serviceAccount.project_id);
    console.log("✅ Private Key 格式檢查:", serviceAccount.private_key.startsWith("-----BEGIN PRIVATE KEY") ? "正確" : "怪怪的");
}

// ... 後面的 code ...

// 3. 取得 FCM 授權 Token
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

// ★★★ 新增：強力日期解析函式 (處理民國年、空格、缺年) ★★★
function parseTwDate(dateStr) {
    if (!dateStr) return null;
    
    // 移除所有空格 (解決 "2 / 9" 格式)
    const cleanStr = dateStr.replace(/\s+/g, '');
    
    // Regex: 支援 "114年1/30" 或 "1/30"
    // Group 1: 年份 (可能 undefined)
    // Group 2,3: 月,日
    // Group 4,5: 時,分
    const regex = /(?:(\d+)年)?(\d+)\/(\d+)[^(]*\(.*?\)(\d+):(\d+)/;
    const match = cleanStr.match(regex);
    
    if (match) {
        let rocYear;
        // 如果有抓到年份
        if (match[1]) {
            rocYear = parseInt(match[1]);
        } else {
            // 如果沒寫年份，預設抓「現在的民國年」
            // 為了避免跨年問題(例如現在12月，活動在1月)，這裡可以寫更複雜的判斷
            // 但目前先預設為「當下年份」，你在 JSON 裡最好手動補上年份比較保險
            const currentRocYear = new Date().getFullYear() - 1911;
            rocYear = currentRocYear; 
        }

        const month = parseInt(match[2]) - 1; // JS 月份是 0-11
        const day = parseInt(match[3]);
        const hour = parseInt(match[4]);
        const minute = parseInt(match[5]);
        
        // 轉成西元
        const year = rocYear + 1911;
        return new Date(year, month, day, hour, minute);
    }
    return null;
}

// ★★★ 輔助：判斷兩個日期是否是同一天 ★★★
function isSameDay(date1, date2) {
    return date1.getFullYear() === date2.getFullYear() &&
           date1.getMonth() === date2.getMonth() &&
           date1.getDate() === date2.getDate();
}

async function main() {
  try {
    // A. 下載 JSON
    console.log("📥 正在下載資料...");
    const response = await axios.get(JSON_URL);
    const data = response.data.data;

    // B. 計算「台灣時間的明天」
    // 1. 取得現在時間的字串 (以台北時區為準)
    const nowTaipeiStr = new Date().toLocaleString("en-US", {timeZone: "Asia/Taipei"});
    const nowTaipei = new Date(nowTaipeiStr);
    
    // 2. 加一天
    const tomorrow = new Date(nowTaipei);
    tomorrow.setDate(tomorrow.getDate() + 1);

    console.log(`🕒 台灣現在: ${nowTaipei.toLocaleString()}`);
    console.log(`🎯 尋找目標: ${tomorrow.toLocaleDateString()} (明天) 的活動`);

    // C. 遍歷資料尋找符合的活動
    let events = []; // 收集所有明天的活動

    for (const [key, value] of Object.entries(data)) {
        const startStr = value['開始時間'];
        if (!startStr) continue;

        // 解析日期
        const eventDate = parseTwDate(startStr);
        
        if (eventDate) {
            // 比對日期 (只比對 年/月/日)
            if (isSameDay(eventDate, tomorrow)) {
                console.log(`✅ 找到活動: ${key} (${startStr})`);
                events.push(key);
            }
        }
    }

    // D. 發送通知
    if (events.length > 0) {
      const title = "選課提醒";
      // 如果有多個活動，用頓號連接： "明天是 初選一、加退選一..."
      const body = `明天是 ${events.join('、')}，記得注意時間喔！`;
      
      console.log(`🚀 準備發送: [${title}] ${body}`);
      
      const accessToken = await getAccessToken();
      
      const payload = {
        message: {
          topic: "all_users",
          notification: {
            title: title,
            body: body,
          },
          // Android 額外設定 (選用)
          android: {
            priority: "high",
            notification: {
                channel_id: "course_alert_channel" 
            }
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
      console.log("📅 明天沒有發現任何活動，略過發送。");
    }

  } catch (error) {
    console.error("❌ 發生錯誤:", error);
    process.exit(1);
  }
}

main();
