using System.IO;
using Barry.Config;
using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;

namespace Barry.Theme.Editor
{
    /// <summary>Menu: Barry/Build Config + Theme Assets. Idempotent; creates missing assets only for config/tokens, rebuilds nothing that exists.</summary>
    public static class BarryThemeBuilder
    {
        const string Root = "Assets/Barry";

        static Color C(string hex, float a = 1f)
        {
            ColorUtility.TryParseHtmlString(hex, out var c);
            c.a = a;
            return c;
        }

        [MenuItem("Barry/Build Config + Theme Assets")]
        public static void Build()
        {
            var body = Font("Inter");
            var mono = Font("JetBrainsMono");
            var brand = Font("Orbitron");

            if (AssetDatabase.LoadAssetAtPath<BarryConfig>(Root + "/Config/BarryConfig.asset") == null)
                AssetDatabase.CreateAsset(ScriptableObject.CreateInstance<BarryConfig>(), Root + "/Config/BarryConfig.asset");

            Save(Galactic(body, mono, brand), "Galactic");
            Save(Light(body, mono, brand), "Light");
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("Barry: config + theme assets ready.");
        }

        static TMP_FontAsset Font(string name)
        {
            string fontPath = $"{Root}/Theme/Fonts/{name}.ttf";
            string assetPath = $"{Root}/Theme/Fonts/{name} SDF.asset";
            var existing = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>(assetPath);
            if (existing != null) return existing;
            var src = AssetDatabase.LoadAssetAtPath<Font>(fontPath);
            var fa = TMP_FontAsset.CreateFontAsset(src, 90, 9, GlyphRenderMode.SDFAA, 1024, 1024, AtlasPopulationMode.Dynamic, true);
            fa.name = name + " SDF";
            AssetDatabase.CreateAsset(fa, assetPath);
            fa.material.name = name + " SDF Material";
            AssetDatabase.AddObjectToAsset(fa.material, fa);
            if (fa.atlasTextures != null && fa.atlasTextures.Length > 0)
            {
                fa.atlasTextures[0].name = name + " SDF Atlas";
                AssetDatabase.AddObjectToAsset(fa.atlasTextures[0], fa);
            }
            EditorUtility.SetDirty(fa);
            return fa;
        }

        static void Save(ThemeTokens t, string name)
        {
            string p = $"{Root}/Theme/Tokens/{name}.asset";
            var old = AssetDatabase.LoadAssetAtPath<ThemeTokens>(p);
            if (old != null) { EditorUtility.CopySerialized(t, old); EditorUtility.SetDirty(old); Object.DestroyImmediate(t); }
            else AssetDatabase.CreateAsset(t, p);
        }

        static ThemeTokens Galactic(TMP_FontAsset b, TMP_FontAsset m, TMP_FontAsset br)
        {
            var t = Common(b, m, br);
            t.id = ThemeId.Galactic;
            t.envSky = C("#0A0F2C");
            t.nebulaA = C("#7C5CFF", .45f); t.nebulaB = C("#2EC5E6", .22f);
            t.planetLight = C("#B9A6FF"); t.planetMid = C("#5B3FD9"); t.planetDark = C("#1E1660"); t.planetRing = C("#C9BEFF");
            t.starColor = Color.white; t.starMinAlpha = .35f;
            t.floor = C("#0D1338"); t.floorGrid = C("#5EE1FF", .28f);
            t.platformFill = C("#151B4A"); t.platformRim = C("#7C5CFF"); t.platformRingDashed = C("#5EE1FF", .60f);
            t.panelBg = C("#FFFFFF", .94f); t.panelBorder = C("#C9BEFF"); t.panelGlow = C("#7C5CFF", .35f);
            t.cameraGlowAlpha = .55f; t.useSoftShadowInsteadOfGlow = false;
            t.accent = C("#5B3FD9"); t.accentSoft = C("#ECE8FD");
            t.rayCore = C("#8FEFFF"); t.rayGlow = C("#5EE1FF", .35f);
            return t;
        }

        static ThemeTokens Light(TMP_FontAsset b, TMP_FontAsset m, TMP_FontAsset br)
        {
            var t = Common(b, m, br);
            t.id = ThemeId.Light;
            t.envSky = C("#F6F4EF");
            t.nebulaA = C("#7C5CFF", 0f); t.nebulaB = C("#2EC5E6", 0f);   // no nebula in Light
            t.planetLight = C("#B9A6FF"); t.planetMid = C("#5B3FD9"); t.planetDark = C("#1E1660"); t.planetRing = C("#C9BEFF");
            t.starColor = Color.white; t.starMinAlpha = 0f;
            t.floor = C("#ECE9E2"); t.floorGrid = C("#DAD5CB");
            t.platformFill = C("#E2DED5"); t.platformRim = C("#DAD5CB"); t.platformRingDashed = C("#DAD5CB");
            t.panelBg = C("#FFFFFF", .94f); t.panelBorder = C("#DAD5CB"); t.panelGlow = new Color(0, 0, 0, .12f);
            t.cameraGlowAlpha = .12f; t.useSoftShadowInsteadOfGlow = true;
            t.accent = C("#1F5FD6"); t.accentSoft = C("#E5EEFC");
            t.rayCore = C("#1F5FD6"); t.rayGlow = C("#1F5FD6", .35f);
            return t;
        }

        static ThemeTokens Common(TMP_FontAsset b, TMP_FontAsset m, TMP_FontAsset br)
        {
            var t = ScriptableObject.CreateInstance<ThemeTokens>();
            t.textPrimary = C("#1F1E1B"); t.textSecondary = C("#5E5D59"); t.brand = C("#2A1F7A");
            t.okText = C("#137A3E"); t.okBg = C("#E6F4EC");
            t.warnText = C("#8A4B06"); t.warnBg = C("#FEF3E2"); t.warnBorder = C("#D98A12");
            t.dangerSolid = C("#C42B2B"); t.dangerText = C("#A3201F"); t.dangerSoft = C("#FDECEC");
            t.stopText = Color.white;
            t.neutralFill = C("#F3F1EC"); t.neutralBorder = C("#DAD5CB");
            t.videoBg = C("#1B1F2E");
            t.overlayDim = C("#080A22", .72f);
            t.cursorFill = Color.white;
            t.fontBody = b; t.fontMono = m; t.fontBrand = br;
            return t;
        }
    }
}
