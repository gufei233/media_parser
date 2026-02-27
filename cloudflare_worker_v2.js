// Cloudflare Workers 反代脚本 v3
// 支持：1. API代理（路径路由） 2. 文件流式下载代理（POST /download）

export default {
  async fetch(request) {
    // 处理 OPTIONS 预检请求
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 200,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "*",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    const url = new URL(request.url);

    // ==================== 文件下载代理（流式） ====================
    // POST /download - 流式代理下载图片/视频文件
    if (request.method === "POST" && url.pathname === "/download") {
      return handleDownload(request);
    }

    // ==================== API代理 ====================
    // GET /douyin/* 或 /ttwid/* - 代理API请求
    return handleApiProxy(request, url);
  },
};

// ========== 下载代理处理器（流式，支持大文件） ==========
async function handleDownload(request) {
  try {
    // 解析请求体
    const body = await request.json();
    const { url: targetUrl, headers: customHeaders } = body;

    if (!targetUrl) {
      return jsonError("缺少 url 参数", 400);
    }
    const parsedTargetUrl = parsePublicHttpUrl(targetUrl);
    if (!parsedTargetUrl) {
      return jsonError("url 非法或指向内网地址", 400);
    }

    console.log(`[下载代理] 目标URL: ${targetUrl}`);

    // 构建请求头
    const headers = new Headers();
    if (customHeaders && typeof customHeaders === "object") {
      let headerCount = 0;
      Object.entries(customHeaders).forEach(([key, value]) => {
        if (headerCount >= 40) return;
        if (typeof key !== "string" || typeof value !== "string") return;
        if (key.length > 128 || value.length > 2048) return;
        headers.set(key, value);
        headerCount += 1;
      });
    }

    // 删除 CF 相关头
    for (const key of [
      "cf-connecting-ip",
      "cf-ipcountry",
      "cf-ray",
      "cf-visitor",
    ]) {
      headers.delete(key);
    }

    // 发起下载请求
    const response = await fetch(parsedTargetUrl.toString(), {
      method: "GET",
      headers: headers,
      redirect: "follow",
    });

    console.log(`[下载代理] 响应状态: ${response.status}`);

    if (!response.ok) {
      return jsonError(
        `上游返回 HTTP ${response.status} ${response.statusText}`,
        502
      );
    }

    // 流式转发：直接将上游响应体转发给客户端，不在内存中缓存
    const responseHeaders = new Headers({
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Expose-Headers": "Content-Length, Content-Type, X-Proxy-Status",
      "X-Proxy-Status": "ok",
    });

    // 转发关键头
    const contentType = response.headers.get("Content-Type");
    if (contentType) {
      responseHeaders.set("Content-Type", contentType);
    }
    const contentLength = response.headers.get("Content-Length");
    if (contentLength) {
      responseHeaders.set("Content-Length", contentLength);
    }
    // 支持客户端 Range 续传
    const contentRange = response.headers.get("Content-Range");
    if (contentRange) {
      responseHeaders.set("Content-Range", contentRange);
    }
    const acceptRanges = response.headers.get("Accept-Ranges");
    if (acceptRanges) {
      responseHeaders.set("Accept-Ranges", acceptRanges);
    }

    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error(`[下载代理] 错误: ${error.message}`);
    return jsonError(error.message, 500);
  }
}

// ========== API代理处理器 ==========
async function handleApiProxy(request, url) {
  const targetHosts = {
    douyin: "www.douyin.com",
    ttwid: "ttwid.bytedance.com",
  };

  const pathMatch = url.pathname.match(/^\/(douyin|ttwid)(\/.*)/);
  if (!pathMatch) {
    return new Response(
      "Invalid path. Use /douyin/* or /ttwid/* for API proxy, or POST /download for file download",
      { status: 400 }
    );
  }

  const targetType = pathMatch[1];
  const targetPath = pathMatch[2];
  const targetHost = targetHosts[targetType];
  const targetUrl = `https://${targetHost}${targetPath}${url.search}`;

  try {
    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);

    for (const key of [
      "cf-connecting-ip",
      "cf-ipcountry",
      "cf-ray",
      "cf-visitor",
    ]) {
      headers.delete(key);
    }

    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow",
    });

    // Encode raw response bytes to base64 to avoid charset-side mojibake.
    const responseBuffer = await response.arrayBuffer();
    const base64Data = arrayBufferToBase64(responseBuffer);

    return new Response(
      JSON.stringify({
        data: base64Data,
        encoding: "base64",
      }),
      {
        status: response.status,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  } catch (error) {
    return jsonError(`代理请求失败: ${error.message}`, 500);
  }
}

// ========== 工具函数 ==========
function jsonError(message, status = 500) {
  return new Response(
    JSON.stringify({
      success: false,
      error: message,
    }),
    {
      status: status,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    }
  );
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

function parsePublicHttpUrl(raw) {
  if (typeof raw !== "string" || !raw) return null;
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  if (!parsed.hostname || isPrivateOrLoopbackHost(parsed.hostname)) return null;
  return parsed;
}

function isPrivateOrLoopbackHost(hostname) {
  const host = String(hostname || "").trim().toLowerCase();
  if (!host) return true;
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (host.endsWith(".local") || host.endsWith(".internal")) return true;

  // IPv4 literal checks.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) {
    const parts = host.split(".").map((v) => Number(v));
    if (parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return true;
    const [a, b] = parts;
    if (a === 10 || a === 127 || a === 0) return true;
    if (a === 169 && b === 254) return true;
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 100 && b >= 64 && b <= 127) return true;
  }

  // IPv6 literal checks (basic).
  if (host.includes(":")) {
    if (host === "::1" || host.startsWith("fe80:") || host.startsWith("fc") || host.startsWith("fd")) {
      return true;
    }
  }

  return false;
}
