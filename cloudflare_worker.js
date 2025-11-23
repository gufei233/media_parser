// Cloudflare Workers反代脚本 - 代理抖音API
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

    // 支持多个目标域名
    const targetHosts = {
      "douyin": "www.douyin.com",
      "ttwid": "ttwid.bytedance.com"
    };

    // 从路径中提取目标类型
    const pathMatch = url.pathname.match(/^\/(douyin|ttwid)(\/.*)/);
    if (!pathMatch) {
      return new Response("Invalid path. Use /douyin/* or /ttwid/*", { status: 400 });
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
        redirect: 'follow'
      });

      // 完全读取响应
      const responseText = await response.text();

      // Base64 编码以绕过 CF 自动压缩
      const base64Data = btoa(unescape(encodeURIComponent(responseText)));

      // 返回 Base64 编码的数据
      return new Response(JSON.stringify({
        data: base64Data,
        encoding: 'base64'
      }), {
        status: response.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
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
  },
};
