using System;
using System.Collections.Generic;
using Barry.Theme;
using Barry.UI.Styling;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace Barry.XR.Tooltips
{
    public enum ControllerHand
    {
        Left,
        Right
    }

    public readonly struct ControllerTooltipDefinition
    {
        public string Id { get; }
        public ControllerHand Hand { get; }
        public string Key { get; }
        public string Label { get; }
        public Vector2 PositionMm { get; }
        public Vector2 SizeMm { get; }
        public Vector2 LeaderStartMm { get; }
        public Vector2 LeaderEndMm { get; }
        public bool IsStop { get; }

        public string PlainText => Key + " " + Label;
        public string RichText => "<b>" + Key + "</b> " + Label;

        public ControllerTooltipDefinition(
            string id,
            ControllerHand hand,
            string key,
            string label,
            Vector2 positionMm,
            Vector2 sizeMm,
            Vector2 leaderStartMm,
            Vector2 leaderEndMm,
            bool isStop = false)
        {
            Id = id;
            Hand = hand;
            Key = key;
            Label = label;
            PositionMm = positionMm;
            SizeMm = sizeMm;
            LeaderStartMm = leaderStartMm;
            LeaderEndMm = leaderEndMm;
            IsStop = isStop;
        }
    }

    public static class ControllerTooltipLayout
    {
        public const float CanvasScale = 0.001f;
        public static readonly Vector2 CanvasSizeMm = new Vector2(360f, 220f);

        private static readonly IReadOnlyList<ControllerTooltipDefinition> definitions =
            Array.AsReadOnly(new[]
            {
                new ControllerTooltipDefinition(
                    "left_move", ControllerHand.Left, "L", "Move · strafe",
                    new Vector2(-117f, -12f), new Vector2(114f, 28f),
                    new Vector2(0f, 24f), new Vector2(-60f, -12f)),
                new ControllerTooltipDefinition(
                    "left_intensity", ControllerHand.Left, "X", "Intensity",
                    new Vector2(128f, -2f), new Vector2(92f, 28f),
                    new Vector2(46f, 21f), new Vector2(82f, -2f)),
                new ControllerTooltipDefinition(
                    "right_turn", ControllerHand.Right, "R", "Turn",
                    new Vector2(130f, -12f), new Vector2(100f, 28f),
                    new Vector2(0f, 24f), new Vector2(80f, -12f)),
                new ControllerTooltipDefinition(
                    "right_stop", ControllerHand.Right, "A", "STOP",
                    new Vector2(-128f, -2f), new Vector2(92f, 28f),
                    new Vector2(-46f, 21f), new Vector2(-82f, -2f),
                    true),
                new ControllerTooltipDefinition(
                    "right_actions", ControllerHand.Right, "B", "Actions",
                    new Vector2(-128f, 58f), new Vector2(92f, 28f),
                    new Vector2(-38f, 44f), new Vector2(-82f, 58f))
            });

        public static IReadOnlyList<ControllerTooltipDefinition> Definitions => definitions;
    }

    [DisallowMultipleComponent]
    internal sealed class TooltipFillEffect : BaseMeshEffect
    {
        public Color FillColor { get; set; } = Color.white;

        public override void ModifyMesh(VertexHelper vertexHelper)
        {
            if (!IsActive()) return;
            UIVertex vertex = default;
            for (int index = 0; index < vertexHelper.currentVertCount; index++)
            {
                vertexHelper.PopulateUIVertex(ref vertex, index);
                vertex.color = FillColor;
                vertexHelper.SetUIVertex(vertex, index);
            }
        }

        public void Refresh(Color color)
        {
            FillColor = color;
            if (graphic != null) graphic.SetVerticesDirty();
        }
    }

    [DisallowMultipleComponent]
    public sealed class ControllerTooltips : MonoBehaviour
    {
        private const float PillRadiusMm = 14f;
        private const float BorderWidthMm = 1f;
        private const float LeaderWidthMm = 1.5f;

        private readonly List<PillView> pills = new List<PillView>();
        private readonly List<Image> leaders = new List<Image>();
        private GameObject leftCanvasRoot;
        private GameObject rightCanvasRoot;
        private ThemeTokens currentTokens;

        public bool TooltipsVisible { get; private set; } = true;
        public Transform LeftTooltipRoot => leftCanvasRoot != null ? leftCanvasRoot.transform : null;
        public Transform RightTooltipRoot => rightCanvasRoot != null ? rightCanvasRoot.transform : null;

        public static ControllerTooltips Create(
            Transform leftController,
            Transform rightController,
            ThemeTokens tokens)
        {
            if (leftController == null) throw new ArgumentNullException(nameof(leftController));
            if (rightController == null) throw new ArgumentNullException(nameof(rightController));

            var managerObject = new GameObject("Controller Tooltips");
            ControllerTooltips tooltips = managerObject.AddComponent<ControllerTooltips>();
            tooltips.leftCanvasRoot = tooltips.CreateControllerCanvas("Left Controller Tooltips", leftController);
            tooltips.rightCanvasRoot = tooltips.CreateControllerCanvas("Right Controller Tooltips", rightController);

            foreach (ControllerTooltipDefinition definition in ControllerTooltipLayout.Definitions)
            {
                Transform root = definition.Hand == ControllerHand.Left
                    ? tooltips.leftCanvasRoot.transform
                    : tooltips.rightCanvasRoot.transform;
                tooltips.leaders.Add(CreateLeader(root, definition));
                tooltips.pills.Add(CreatePill(root, definition, tokens));
            }

            tooltips.ApplyTheme(tokens != null ? tokens : ThemeApplier.Current);
            tooltips.SetVisible(true);
            return tooltips;
        }

        private void OnEnable()
        {
            ThemeApplier.Changed += ApplyTheme;
        }

        private void OnDisable()
        {
            ThemeApplier.Changed -= ApplyTheme;
        }

        private void OnDestroy()
        {
            DestroyOwned(leftCanvasRoot);
            DestroyOwned(rightCanvasRoot);
        }

        public void SetVisible(bool visible)
        {
            TooltipsVisible = visible;
            if (leftCanvasRoot != null) leftCanvasRoot.SetActive(visible);
            if (rightCanvasRoot != null) rightCanvasRoot.SetActive(visible);
            if (visible && isActiveAndEnabled)
            {
                // Children re-subscribe when re-enabled; move our theme handler
                // behind theirs so neutral/danger overrides remain final.
                enabled = false;
                enabled = true;
                ApplyTheme(currentTokens != null ? currentTokens : ThemeApplier.Current);
            }
        }

        public void ApplyTheme(ThemeTokens tokens)
        {
            currentTokens = tokens;
            Color leaderColor = Hex("B9AEEA");
            Color normalText = tokens != null ? tokens.textPrimary : Hex("1F1E1B");
            Color neutralBorder = tokens != null ? tokens.neutralBorder : Hex("DAD5CB");
            Color danger = tokens != null ? tokens.dangerSolid : Hex("C42B2B");
            Color stopText = tokens != null ? tokens.stopText : Color.white;

            foreach (Image leader in leaders)
                if (leader != null) leader.color = leaderColor;

            foreach (PillView pill in pills)
            {
                if (pill.Root == null) continue;
                pill.Background.RefreshFromTheme(tokens);
                pill.Background.SetStyle(
                    PillRadiusMm,
                    BorderWidthMm,
                    0f,
                    pill.Definition.IsStop ? danger : neutralBorder,
                    Color.clear);
                pill.Fill.Refresh(pill.Definition.IsStop ? danger : Color.white);
                pill.Text.color = pill.Definition.IsStop ? stopText : normalText;
                pill.Text.font = tokens != null ? tokens.fontBody : null;
            }
        }

        private GameObject CreateControllerCanvas(string name, Transform controller)
        {
            var root = new GameObject(name, typeof(RectTransform), typeof(Canvas));
            RectTransform rect = root.GetComponent<RectTransform>();
            rect.SetParent(controller, false);
            rect.sizeDelta = ControllerTooltipLayout.CanvasSizeMm;
            rect.localScale = Vector3.one * ControllerTooltipLayout.CanvasScale;
            rect.localPosition = Vector3.zero;
            rect.localRotation = Quaternion.identity;
            Canvas canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.overrideSorting = true;
            canvas.sortingOrder = 20;
            return root;
        }

        private static PillView CreatePill(
            Transform parent,
            ControllerTooltipDefinition definition,
            ThemeTokens tokens)
        {
            var root = new GameObject(definition.Id, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            RectTransform rect = root.GetComponent<RectTransform>();
            rect.SetParent(parent, false);
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = definition.PositionMm;
            rect.sizeDelta = definition.SizeMm;

            Image image = root.GetComponent<Image>();
            image.raycastTarget = false;
            RoundedPanel rounded = RoundedPanel.Apply(image, PillRadiusMm, false, 0f);
            TooltipFillEffect fill = root.AddComponent<TooltipFillEffect>();

            var textObject = new GameObject("Label", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            RectTransform textRect = textObject.GetComponent<RectTransform>();
            textRect.SetParent(rect, false);
            textRect.anchorMin = Vector2.zero;
            textRect.anchorMax = Vector2.one;
            textRect.offsetMin = Vector2.zero;
            textRect.offsetMax = Vector2.zero;
            TextMeshProUGUI text = textObject.GetComponent<TextMeshProUGUI>();
            text.text = definition.RichText;
            text.richText = true;
            text.fontSize = 13f;
            text.fontStyle = definition.IsStop ? FontStyles.Bold : FontStyles.Normal;
            text.alignment = TextAlignmentOptions.Center;
            text.textWrappingMode = TextWrappingModes.NoWrap;
            text.raycastTarget = false;
            if (tokens != null) text.font = tokens.fontBody;

            return new PillView(definition, root, image, rounded, fill, text);
        }

        private static Image CreateLeader(Transform parent, ControllerTooltipDefinition definition)
        {
            var lineObject = new GameObject(definition.Id + " Leader", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            RectTransform rect = lineObject.GetComponent<RectTransform>();
            rect.SetParent(parent, false);
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
            Vector2 delta = definition.LeaderEndMm - definition.LeaderStartMm;
            rect.anchoredPosition = (definition.LeaderStartMm + definition.LeaderEndMm) * 0.5f;
            rect.sizeDelta = new Vector2(delta.magnitude, LeaderWidthMm);
            rect.localRotation = Quaternion.Euler(0f, 0f, Mathf.Atan2(delta.y, delta.x) * Mathf.Rad2Deg);
            Image image = lineObject.GetComponent<Image>();
            image.raycastTarget = false;
            return image;
        }

        private static Color Hex(string value)
        {
            return ColorUtility.TryParseHtmlString("#" + value, out Color color) ? color : Color.magenta;
        }

        private static void DestroyOwned(GameObject owned)
        {
            if (owned == null) return;
            if (Application.isPlaying) Destroy(owned);
            else DestroyImmediate(owned);
        }

        private sealed class PillView
        {
            public readonly ControllerTooltipDefinition Definition;
            public readonly GameObject Root;
            public readonly Image Image;
            public readonly RoundedPanel Background;
            public readonly TooltipFillEffect Fill;
            public readonly TextMeshProUGUI Text;

            public PillView(
                ControllerTooltipDefinition definition,
                GameObject root,
                Image image,
                RoundedPanel background,
                TooltipFillEffect fill,
                TextMeshProUGUI text)
            {
                Definition = definition;
                Root = root;
                Image = image;
                Background = background;
                Fill = fill;
                Text = text;
            }
        }
    }
}
