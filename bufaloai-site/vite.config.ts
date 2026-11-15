import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteStaticCopy } from 'vite-plugin-static-copy'
export default defineConfig({
  plugins: [
    react(),
    // Static hosting must also serve direct requests and refreshes at both entry routes.
    {
      name: 'bufaloai-entry-routes',
      enforce: 'post',
      generateBundle(_, bundle) {
        const entry = bundle['index.html']
        if (entry?.type === 'asset') {
          for (const route of ['intro', 'app'])
            this.emitFile({
              type: 'asset',
              fileName: route + '/index.html',
              source: entry.source,
            })
        }
      },
    },
    viteStaticCopy({
      targets: ['Cesium.js', 'Assets', 'Widgets', 'Workers', 'ThirdParty'].map(
        (name) => ({
          src: 'node_modules/cesium/Build/Cesium/' + name,
          dest: 'cesium',
        }),
      ),
    }),
  ],
})
