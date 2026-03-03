import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model

# --- 1. CONFIGURATION DE L'INTERFACE ---
st.set_page_config(page_title="Système RH | Matrice EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
div.stDownloadButton > button {
    background-color: #28a745 !important;
    color: white !important;
    font-size: 18px !important;
    font-weight: bold !important;
    padding: 15px 30px !important;
    border-radius: 8px !important;
    border: none !important;
    width: 100% !important;
}
div.stDownloadButton > button:hover {
    background-color: #218838 !important;
    border-color: #1e7e34 !important;
}
</style>
""", unsafe_allow_html=True)

# --- 2. MENU LATÉRAL ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=60) 
    st.title("Configuration")
    st.markdown("---")
    
    nb_semaines = st.number_input("⏱️ Durée du cycle (semaines)", min_value=4, max_value=12, value=4, step=4)
    
    st.markdown("---")
    st.caption("🔒 Moteur de Matrice v13.1 (Élastique & Propre)")
    st.caption("✓ Exactement 2 Remplaçants\n✓ Renforts auto (Matin)\n✓ Lissage intelligent des Coupés\n✓ Roulement Infini\n✓ Max 4j consécutifs")

# --- 3. ESPACE CENTRAL ---
st.title("Génération de la Matrice de Roulement")
st.info("💡 Matrice élastique : Ajoutez un titulaire, il prendra automatiquement les postes en renfort le matin et soulagera les coupés de l'équipe, tout en gardant exactement 2 remplaçants fixes le week-end.")

col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
with col_kpi1:
    st.metric(label="Jours de la matrice", value=nb_semaines * 7)
with col_kpi2:
    st.metric(label="Titulaires le WE", value="9")
with col_kpi3:
    st.metric(label="Remplaçants fixes", value="2")

st.markdown("<br>", unsafe_allow_html=True)

data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3
})

st.subheader("Configuration de l'équipe titulaire")
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("🚀 GÉNÉRER LA MATRICE INTELLIGENTE", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    
    # 🛑 CORRECTION ICI : Retour STRICT à exactement 2 remplaçants. Pas une ligne de plus.
    noms_complets = noms_titulaires + ["REMPLAÇANT 1", "REMPLAÇANT 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Analyse élastique de l'effectif et lissage des contraintes (environ 45-60 secondes)..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        cibles_travail = [] 
        penalites_c = [] 
        mult_cycle = nb_semaines // 4 
        
        # --- LOGIQUE TITULAIRES ---
        for e in range(nb_titulaires):
            cible_jours = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            cibles_travail.append(cible_jours)
            
            # Contrat respecté au jour près
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            
            # Repos Circulaire (A -> M interdit)
            for d in range(jours_cycle): 
                model.AddImplication(x[(e, d, 'A')], x[(e, (d + 1) % jours_cycle, 'M')].Not())
            
            # Max 4 Jours Circulaire
            for d in range(jours_cycle): 
                model.Add(sum(x[(e, (d+i) % jours_cycle, p)] for i in range(5) for p in postes) <= 4)
            
            # LISSAGE ÉLASTIQUE DES COUPÉS ('C')
            j_sem = [d for d in range(jours_cycle) if d % 7 < 5]
            j_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            c_sem_var = sum(x[(e, d, 'C')] for d in j_sem)
            c_we_var = sum(x[(e, d, 'C')] for d in j_we)
            
            # Limites absolues physiques
            model.Add(c_sem_var + c_we_var <= 2 * mult_cycle)
            model.Add(c_we_var <= 1 * mult_cycle)
            
            # Pénalité pour l'IA si elle donne plus de 1 coupé en semaine
            temp_excess = model.NewIntVar(-100, 100, f'temp_excess_{e}')
            model.Add(temp_excess == c_sem_var - (1 * mult_cycle))
            excess_c_sem = model.NewIntVar(0, 100, f'excess_c_sem_{e}')
            model.AddMaxEquality(excess_c_sem, [0, temp_excess])
            penalites_c.append(excess_c_sem)
            
            # Week-ends Alternés
            ind_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif = model.NewBoolVar(f'we_a_{e}_{w}')
                model.Add(sum(x[(e, sat, p)] for p in postes) == sum(x[(e, sun, p)] for p in postes))
                model.AddMaxEquality(actif, [x[(e, sat, p)] for p in postes])
                ind_we.append(actif)
                model.Add(sum(x[(e, d, p)] for d in range(w*7, w*7+7) for p in postes) <= 4 + (2 * actif))

            for w in range(nb_semaines): 
                model.Add(ind_we[w] + ind_we[(w + 1) % nb_semaines] <= 1)

        # --- LOGIQUE REMPLAÇANTS (Uniquement WE) ---
        for e in range(nb_titulaires, total_effectif):
            cibles_travail.append("Remp.") 
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 < 5: 
                    for p in postes: model.Add(x[(e, d, p)] == 0) # Zéro en semaine
                if d % 7 == 5: 
                    model.Add(sum(x[(e, d, p)] for p in postes) == sum(x[(e, d+1, p)] for p in postes))
                model.Add(x[(e, d, 'C')] == 0)

        # --- QUOTAS EHPAD (AVEC RENFORTS AUTOMATIQUES) ---
        for d in range(jours_cycle):
            is_we = (d % 7 >= 5)
            m_t, a_t, c_t = (6, 3, 2) if is_we else (8, 4, 1)
            
            # Le '>=' sur le matin permet d'absorber les soignants supplémentaires
            model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) >= m_t) 
            model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == a_t)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == c_t)
            if is_we:
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires) for p in postes) == 9)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for p in postes) == 2)

        # --- OPTIMISATION GLOBALE ---
        # L'IA va chercher à minimiser les coupés excessifs en semaine
        model.Minimize(sum(penalites_c))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 75.0 
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                ligne, j_t, c_s, c_w = [], 0, 0, 0
                for d in range(jours_cycle):
                    v = "Repos"
                    for p in postes:
                        if solver.Value(x[(e, d, p)]) == 1: 
                            v, j_t = p, j_t + 1
                            if p == 'C':
                                if d % 7 < 5: c_s += 1
                                else: c_w += 1
                    ligne.append(v)
                
                enchain_am = sum(1 for d in range(jours_cycle) if ligne[d] == 'A' and ligne[(d+1)%jours_cycle] == 'M')
                resultats.append(ligne)
                noms_utilises.append(noms_complets[e])
                
                if e < nb_titulaires:
                    etoile = "🌟 " if c_s > 1 * mult_cycle else "✅ "
                    audit_data.append(f"{etoile}{j_t}j | {c_s}C Sem / {c_w}C WE | {enchain_am} A->M")
                else:
                    audit_data.append(f"VACATION | {j_t}j WE")

            # --- EXCEL ---
            df_final = pd.DataFrame(resultats, index=noms_utilises)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Matrice', startrow=3, header=False)
                wb, ws = writer.book, writer.sheets['Matrice']
                
                f_titre = wb.add_format({'bold': True, 'font_size': 16, 'align': 'center', 'bg_color': '#2C3E50', 'font_color': 'white'})
                f_sem = wb.add_format({'bold': True, 'bg_color': '#D5DBDB', 'border': 1, 'align': 'center'})
                f_jour = wb.add_format({'bold': True, 'bg_color': '#EAEDED', 'border': 1, 'align': 'center'})
                f_m = wb.add_format({'bg_color': '#E0F2F1', 'font_color': '#00695C', 'align': 'center', 'border': 1})
                f_a = wb.add_format({'bg_color': '#FFF3E0', 'font_color': '#E65100', 'align': 'center', 'border': 1})
                f_c = wb.add_format({'bg_color': '#FFEBEE', 'font_color': '#B71C1C', 'align': 'center', 'border': 1})
                f_remp = wb.add_format({'bg_color': '#34495E', 'font_color': 'white', 'bold': True, 'align': 'center', 'border': 1})
                f_audit = wb.add_format({'font_color': '#34495E', 'bold': True, 'border': 1, 'bg_color': '#F4F6F6'})
                
                ws.set_column('A:A', 25)
                ws.set_column(1, jours_cycle, 6)
                ws.set_column(jours_cycle+1, jours_cycle+1, 40)
                ws.freeze_panes(3, 1)
                
                ws.merge_range(0, 0, 0, jours_cycle+1, "MATRICE DE ROULEMENT PERPÉTUELLE EHPAD", f_titre)
                for w in range(nb_semaines):
                    ws.merge_range(1, (w*7)+1, 1, (w*7)+7, f"SEMAINE {w+1}", f_sem)
                ws.write(2, 0, "Employés", f_jour)
                jours_lettres = ["L", "M", "M", "J", "V", "S", "D"]
                for c in range(jours_cycle): ws.write(2, c+1, jours_lettres[c%7], f_jour)
                ws.write(2, jours_cycle+1, "AUDIT QUALITÉ TRAME", f_jour)
                
                for r in range(len(noms_utilises)):
                    is_remp = "REMPLAÇANT" in noms_utilises[r]
                    for c in range(jours_cycle):
                        val = resultats[r][c]
                        fmt = f_remp if is_remp and val != "Repos" else f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else None
                        ws.write(r+3, c+1, val, fmt)
                    ws.write(r+3, jours_cycle+1, audit_data[r], f_audit)
            
            st.success("✅ Matrice de roulement générée (Propre et Élégante) !")
            st.download_button("📥 TÉLÉCHARGER LA MATRICE", buffer.getvalue(), "Matrice_Roulement_Parfaite.xlsx")
        else:
            st.error("❌ Les contraintes sont trop strictes pour l'effectif actuel.")
