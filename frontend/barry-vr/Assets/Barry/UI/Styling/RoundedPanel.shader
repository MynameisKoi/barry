Shader "Barry/UI/RoundedPanel"
{
    Properties
    {
        [PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {}
        _StencilComp ("Stencil Comparison", Float) = 8
        _Stencil ("Stencil ID", Float) = 0
        _StencilOp ("Stencil Operation", Float) = 0
        _StencilWriteMask ("Stencil Write Mask", Float) = 255
        _StencilReadMask ("Stencil Read Mask", Float) = 255
        _ColorMask ("Color Mask", Float) = 15
        [Toggle(UNITY_UI_ALPHACLIP)] _UseUIAlphaClip ("Use Alpha Clip", Float) = 0
    }

    SubShader
    {
        Tags
        {
            "Queue" = "Transparent"
            "IgnoreProjector" = "True"
            "RenderType" = "Transparent"
            "PreviewType" = "Plane"
            "CanUseSpriteAtlas" = "True"
            "RenderPipeline" = "UniversalPipeline"
        }

        Stencil
        {
            Ref [_Stencil]
            Comp [_StencilComp]
            Pass [_StencilOp]
            ReadMask [_StencilReadMask]
            WriteMask [_StencilWriteMask]
        }

        Cull Off
        Lighting Off
        ZWrite Off
        ZTest [unity_GUIZTestMode]
        Blend SrcAlpha OneMinusSrcAlpha
        ColorMask [_ColorMask]

        Pass
        {
            Name "RoundedPanel"

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 2.0
            #pragma multi_compile_local _ UNITY_UI_CLIP_RECT
            #pragma multi_compile_local _ UNITY_UI_ALPHACLIP

            #include "UnityCG.cginc"
            #include "UnityUI.cginc"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float4 color : COLOR;
                float2 uv0 : TEXCOORD0;
                float4 panelPosition : TEXCOORD1;
                float4 panelShape : TEXCOORD2;
                float4 panelEffect : TEXCOORD3;
                float3 borderColor : NORMAL;
                float4 effectColor : TANGENT;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float4 color : COLOR;
                float2 uv0 : TEXCOORD0;
                float4 panelPosition : TEXCOORD1;
                float4 panelShape : TEXCOORD2;
                float4 panelEffect : TEXCOORD3;
                float3 borderColor : TEXCOORD4;
                float4 effectColor : TEXCOORD5;
                float4 worldPosition : TEXCOORD6;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            sampler2D _MainTex;
            float4 _TextureSampleAdd;
            float4 _ClipRect;

            Varyings vert(Attributes input)
            {
                Varyings output;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);
                output.worldPosition = input.positionOS;
                output.positionCS = UnityObjectToClipPos(input.positionOS);
                output.color = input.color;
                output.uv0 = input.uv0;
                output.panelPosition = input.panelPosition;
                output.panelShape = input.panelShape;
                output.panelEffect = input.panelEffect;
                output.borderColor = input.borderColor;
                output.effectColor = input.effectColor;
                return output;
            }

            float RoundedRectangleDistance(float2 panelPoint, float2 halfSize, float radius)
            {
                radius = min(radius, min(halfSize.x, halfSize.y));
                float2 q = abs(panelPoint) - halfSize + radius;
                return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
            }

            half4 frag(Varyings input) : SV_Target
            {
                float2 panelPoint = input.panelPosition.xy;
                float2 halfSize = input.panelShape.xy;
                float radius = input.panelShape.z;
                float borderWidth = input.panelShape.w;
                float effectSize = input.panelEffect.x;
                float effectStrength = saturate(input.panelEffect.y);
                float borderAlpha = saturate(input.panelEffect.z);
                bool softShadow = input.panelEffect.w > 0.5;

                float distanceToPanel = RoundedRectangleDistance(panelPoint, halfSize, radius);
                float antialias = max(fwidth(distanceToPanel), 0.001);
                float outerCoverage = 1.0 - smoothstep(-antialias, antialias, distanceToPanel);
                float innerCoverage = 1.0 - smoothstep(
                    -borderWidth - antialias,
                    -borderWidth + antialias,
                    distanceToPanel);

                half4 sprite = tex2D(_MainTex, input.uv0) + _TextureSampleAdd;
                half4 fill = input.color * sprite;
                half4 border = half4(input.borderColor, borderAlpha);
                half4 panel = lerp(border, fill, innerCoverage);
                panel.a *= outerCoverage;

                float2 effectPoint = panelPoint;
                if (softShadow) effectPoint.y += effectSize * 0.18;
                float effectDistance = RoundedRectangleDistance(effectPoint, halfSize, radius);
                float outsideDistance = max(effectDistance, 0.0);
                float effectFalloff = effectSize > 0.001
                    ? 1.0 - smoothstep(0.0, effectSize, outsideDistance)
                    : 0.0;
                half effectAlpha = input.effectColor.a * effectStrength * effectFalloff;

                half outputAlpha = panel.a + effectAlpha * (1.0 - panel.a);
                half3 outputRgb = outputAlpha > 0.0001
                    ? (panel.rgb * panel.a + input.effectColor.rgb * effectAlpha * (1.0 - panel.a)) / outputAlpha
                    : 0.0;
                half4 result = half4(outputRgb, outputAlpha);

                #ifdef UNITY_UI_CLIP_RECT
                result.a *= UnityGet2DClipping(input.worldPosition.xy, _ClipRect);
                #endif

                #ifdef UNITY_UI_ALPHACLIP
                clip(result.a - 0.001);
                #endif
                return result;
            }
            ENDCG
        }
    }
}
