Shader "Barry/Environment/ProceduralSkybox"
{
    Properties
    {
        _SkyColor ("Sky", Color) = (0.039,0.059,0.173,1)
        _NebulaA ("Nebula A", Color) = (0.486,0.361,1,0.45)
        _NebulaB ("Nebula B", Color) = (0.18,0.773,0.902,0.22)
        _StarColor ("Stars", Color) = (1,1,1,1)
        _StarMinAlpha ("Minimum Star Alpha", Range(0,1)) = 0.35
    }
    SubShader
    {
        Tags { "Queue"="Background" "RenderType"="Background" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes { float4 positionOS : POSITION; };
            struct Varyings { float4 positionCS : SV_POSITION; float3 direction : TEXCOORD0; };
            CBUFFER_START(UnityPerMaterial)
                half4 _SkyColor, _NebulaA, _NebulaB, _StarColor;
                half _StarMinAlpha;
            CBUFFER_END

            Varyings Vert(Attributes input)
            {
                Varyings output;
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
                output.direction = input.positionOS.xyz;
                return output;
            }

            float Hash31(float3 p)
            {
                p = frac(p * 0.1031);
                p += dot(p, p.yzx + 33.33);
                return frac((p.x + p.y) * p.z);
            }

            half4 Frag(Varyings input) : SV_Target
            {
                float3 direction = normalize(input.direction);
                float glowA = pow(saturate((dot(direction, normalize(float3(0.0, 0.08, 1.0))) - 0.55) / 0.45), 2.0);
                float glowB = pow(saturate((dot(direction, normalize(float3(-0.42, 0.20, 1.0))) - 0.72) / 0.28), 2.0);

                float3 cell = floor(direction * 420.0);
                float starSeed = Hash31(cell);
                float star = step(0.992, starSeed);
                float brightness = lerp(_StarMinAlpha, 1.0, Hash31(cell + 19.7));

                half3 color = _SkyColor.rgb;
                color += _NebulaA.rgb * (_NebulaA.a * glowA);
                color += _NebulaB.rgb * (_NebulaB.a * glowB);
                color += _StarColor.rgb * (star * brightness * _StarColor.a);
                return half4(color, 1.0);
            }
            ENDHLSL
        }
    }
}
