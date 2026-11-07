/** Jest transformer that compiles ESM `.js` files to CJS using esbuild.
 *
 * @nestjs/typeorm v12 is ESM-only (`"type": "module"`), which Jest 29 in CJS
 * mode cannot load directly. This transformer converts those files (and any
 * other allowlisted `.js` files from `node_modules`) to CommonJS so the test
 * suites can import them.
 */
const esbuild = require('esbuild');
const crypto = require('crypto');

module.exports = {
  process(src, filename, config, options) {
    // `import.meta.url` is undefined after ESM→CJS transform, which breaks
    // `createRequire(import.meta.url)` used by @nestjs/typeorm. Substitute the
    // source filename so createRequire receives a valid path.
    src = src.replace(/import\.meta\.url/g, `'${filename.replace(/'/g, "\\'")}'`);
    const result = esbuild.transformSync(src, {
      loader: 'js',
      format: 'cjs',
      target: 'es2022',
      sourcefile: filename,
      sourcemap: true,
    });
    return {
      code: result.code,
      map: result.map ? JSON.parse(result.map) : undefined,
    };
  },

  getCacheKey(src, filename, configString) {
    const configStr = typeof configString === 'string' ? configString : JSON.stringify(configString);
    return crypto
      .createHash('md5')
      .update(src)
      .update('\0')
      .update(configStr)
      .digest('hex');
  },
};
