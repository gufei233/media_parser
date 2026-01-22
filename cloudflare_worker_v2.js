// Cloudflare Workers 反代脚本 v2
// 支持：1. API代理（路径路由） 2. 文件下载代理（POST /download）

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

    // ==================== 新功能：下载代理 ====================
    // POST /download - 下载图片/视频文件
    if (request.method === "POST" && url.pathname === "/download") {
      return handleDownload(request);
    }

    // ==================== 原功能：API代理 ====================
    // GET /douyin/* 或 /ttwid/* - 代理API请求
    return handleApiProxy(request, url);
  },
};

// ========== 下载代理处理器 ==========
async function handleDownload(request) {
  try {
    // 解析请求体
    const body = await request.json();
    const { url: targetUrl, headers: customHeaders } = body;

    if (!targetUrl) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "缺少 url 参数",
        }),
        {
          status: 400,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          },
        }
      );
    }

    console.log(`[下载代理] 目标URL: ${targetUrl}`);

    // 构建请求头
    const headers = new Headers();
    if (customHeaders) {
      Object.entries(customHeaders).forEach(([key, value]) => {
        headers.set(key, value);
      });
    }

    // 删除可能导致问题的头
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");

    // 发起下载请求
    const response = await fetch(targetUrl, {
      method: "GET",
      headers: headers,
      redirect: "follow",
    });

    console.log(`[下载代理] 响应状态: ${response.status}`);

    if (!response.ok) {
      return new Response(
        JSON.stringify({
          success: false,
          error: `HTTP ${response.status} ${response.statusText}`,
        }),
        {
          status: 200, // 外层返回200，内层标记失败
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          },
        }
      );
    }

    // 读取二进制数据
    const arrayBuffer = await response.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);

    // Base64 编码
    const base64Content = btoa(String.fromCharCode(...bytes));

    console.log(`[下载代理] 下载成功，大小: ${bytes.length} bytes`);

    // 返回成功响应
    return new Response(
      JSON.stringify({
        success: true,
        content: base64Content,
        size: bytes.length,
        contentType: response.headers.get("Content-Type"),
      }),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  } catch (error) {
    console.error(`[下载代理] 错误: ${error.message}`);
    return new Response(
      JSON.stringify({
        success: false,
        error: error.message,
      }),
      {
        status: 500,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}

// ========== API代理处理器（保持原有逻辑）==========
async function handleApiProxy(request, url) {
  // 支持多个目标域名
  const targetHosts = {
    douyin: "www.douyin.com",
    ttwid: "ttwid.bytedance.com",
  };

  // 从路径中提取目标类型
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

  // 构建目标请求URL
  const targetUrl = `https://${targetHost}${targetPath}${url.search}`;

  try {
    // 复制请求头
    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);

    // 删除 CF 相关头
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");

    // 发起请求
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow",
    });

    // 完全读取响应
    const responseText = await response.text();

    // Base64 编码以绕过 CF 自动压缩
    const base64Data = btoa(unescape(encodeURIComponent(responseText)));

    // 返回 Base64 编码的数据（保持原有格式）
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
    return new Response(
      JSON.stringify({
        error: "代理请求失败",
        message: error.message,
      }),
      {
        status: 500,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}
