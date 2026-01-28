// scripts/notify_from_json.js
const axios = require('axios');
const { google } = require('googleapis');

// 1. 設定：你的 JSON 網址
const JSON_URL = "https://edwinchu0711.github.io/CourseSelectionDateUpdate/data.json";

// 2. 從環境變數讀取 Firebase Service Account
// let serviceAccount = JSON.parse(process.env.FIREBASE_KEY);
let serviceAccount;
// ... 前面的 code ...

const rawKey = process.env.FIREBASE_KEY;

if (!rawKey) {
    console.error("❌ 錯誤: 找不到環境變數 FIREBASE_KEY");
    process.exit(1);
}

// 簡單的除錯資訊 (不會洩漏金鑰，只看頭尾)
console.log(`🔑 讀取到的 Key 長度: ${rawKey.length}`);
console.log(`👀 開頭字元: '${rawKey.substring(0, 1)}'`); // 應該要是 {
console.log(`👀 結尾字元: '${rawKey.substring(rawKey.length - 1)}'`); // 應該要是 }

try {
    // 嘗試清洗字串 (去除前後空白、去除可能被意外加入的引號)
    let cleanKey = rawKey.trim();
    
    // 有時候 GitHub Secret 會不小心多包一層引號，這裡做防呆
    if (cleanKey.startsWith("'") && cleanKey.endsWith("'")) {
        cleanKey = cleanKey.slice(1, -1);
    }
    if (cleanKey.startsWith('"') && cleanKey.endsWith('"')) {
        cleanKey = cleanKey.slice(1, -1);
    }

    serviceAccount = JSON.parse(cleanKey);
    console.log("✅ JSON 格式解析成功！");

} catch (e) {
    console.error("❌ JSON 解析失敗！內容格式錯誤。");
    console.error("錯誤細節:", e.message);
    // 這裡可以印出前 10 個字元幫助判斷，但不要印全部
    console.error("前 10 個字元:", rawKey.substring(0, 10));
    process.exit(1);
}



// 3. 取得 FCM 授權 Token
// 3. 取得 FCM 授權 Token
function getAccessToken() {
  return new Promise(function(resolve, reject) {
    const jwtClient = new google.auth.JWT({
      email: serviceAccount.client_email,
      key: serviceAccount.private_key, // 這裡會用到我們之前 replace 過的正確金鑰
      scopes: ['https://www.googleapis.com/auth/firebase.messaging']
    });

    jwtClient.authorize(function(err, tokens) {
      if (err) {
        console.error("🔑 授權失敗，請檢查 private_key 是否正確");
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

// ... (前面的 getAccessToken, parseTwDate, isSameDay 等函式保持不變) ...

async function main() {
  try {
    // A. 下載 JSON
    console.log("📥 正在下載資料...");
    const response = await axios.get(JSON_URL);
    const data = response.data.data;

    // B. 設定基準時間 (台灣時間)
    const nowTaipeiStr = new Date().toLocaleString("en-US", {timeZone: "Asia/Taipei"});
    const today = new Date(nowTaipeiStr);
    
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);

    console.log(`🕒 台灣現在: ${today.toLocaleString()}`);

    // C. 遍歷資料與過濾
    let todayEvents = [];
    let tomorrowEvents = [];

    // 定義允許的名稱 (白名單)
    const allowedNames = ["初選一", "初選二", "加退選一", "加退選二", "異常處理", "選課確認", "棄選時間"];

    for (const [key, value] of Object.entries(data)) {
        const startStr = value['開始時間'];
        if (!startStr) continue;

        let shouldNotify = false;
        
        // 1. 檢查是否為「課程查詢」且符合格式 (e.g., 114-1 或 114-2)
        if (key.includes("課程查詢")) {
            if (/-\d/.test(key)) { // 檢查是否有 "-數字"
                shouldNotify = true;
            }
        } 
        // 2. 檢查是否在白名單內
        else if (allowedNames.includes(key)) {
            shouldNotify = true;
        }

        if (!shouldNotify) continue;

        // 3. 解析日期並比對
        const eventDate = parseTwDate(startStr);
        if (eventDate) {
            if (isSameDay(eventDate, today)) {
                todayEvents.push(key);
            } else if (isSameDay(eventDate, tomorrow)) {
                tomorrowEvents.push(key);
            }
        }
    }

    // D. 發送通知邏輯
    const sendNotification = async (events, dayLabel) => {
        if (events.length === 0) return;

        const title = `選課提醒 (${dayLabel})`;
        const body = `${dayLabel}是 ${events.join('、')}，記得注意時間喔！`;
        
        console.log(`🚀 準備發送: [${title}] ${body}`);
        
        const accessToken = await getAccessToken();
        const payload = {
            message: {
                topic: "all_users",
                notification: { title: title, body: body },
                android: { 
                    priority: "high", 
                    notification: { channel_id: "course_alert_channel" } 
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
    };

    // 分別執行今天與明天的通知
    if (todayEvents.length > 0) {
        await sendNotification(todayEvents, "今天");
    } else {
        console.log("📅 今天無符合項目。");
    }

    if (tomorrowEvents.length > 0) {
        await sendNotification(tomorrowEvents, "明天");
    } else {
        console.log("📅 明天無符合項目。");
    }

    console.log("✅ 流程結束。");

  } catch (error) {
    console.error("❌ 發生錯誤:", error);
    process.exit(1);
  }
}

main();

