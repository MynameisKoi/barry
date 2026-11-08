Shader "Barry/Environment/PlatformDisc"
{
    Properties
    {
        _FillColor ("Fill", Color) = (0.082,0.106,0.29,1)
        _RimColor ("Rim", Color) = (0.486,0.361,1,1)
        _DashedRingColor ("Dashed Ring", Color) = (0.369,0.882,1,0.6)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "RenderPipeline"="UniversalPipeline" }
        Cull Off
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; };
            struct Varyings { float4 positionCS : SV_POSITION; float2 uv : TEXCOORD0; };
            CBUFFER_START(UnityPerMaterial)
                half4 _FillColor, _RimColor, _DashedRingColor;
            CBUFFER_END
            Varyings Vert(Attributes input)
            {
                Varyings output;
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
                output.uv = input.uv;
                return output;
            }
            half4 Frag(Varyings input) : SV_Target
            {
                float2 centered = input.uv - 0.5;
                float radius = length(centered);
                float rim = smoothstep(0.465, 0.49, radius);
                float ringBand = 1.0 - smoothstep(0.012, 0.025, abs(radius - 0.39));
                float angle = atan2(centered.y, centered.x) / 6.2831853 + 0.5;
                float dash = step(0.38, frac(angle * 36.0));
                half3 color = lerp(_FillColor.rgb, _RimColor.rgb, rim * _RimColor.a);
                color = lerp(color, _DashedRingColor.rgb, ringBand * dash * _DashedRingColor.a);
                return half4(color, 1.0);
            }
            ENDHLSL
        }
    }
}
